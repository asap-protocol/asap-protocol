"""v1.3.0 End-to-End Showcase: Delegation, Metering, and SLA.

Demonstrates all v1.3.0 features working together:
1. Delegation: Agent A generates a token for Agent B with max_tasks=5
2. Metering: Agent B performs tasks; usage is logged locally
3. Transparency: Agent A queries GET /usage to see Agent B's consumption
4. Trust/SLA: Agent B artificially injects a delay -> Agent A receives
   an SLA breach alert via WebSocket

Run:
    uv run python -m asap.examples.v1_3_0_showcase
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import Any, cast

import httpx
from joserfc import jwk, jwt as jose_jwt
from websockets.asyncio.client import connect as ws_connect

from asap.auth import OAuth2Config
from asap.crypto.keys import generate_keypair
from asap.economics import (
    InMemoryDelegationStorage,
    InMemoryMeteringStorage,
    InMemorySLAStorage,
    MeteringQuery,
    UsageMetrics,
    compute_error_rate_percent,
    compute_latency_p95_ms,
)
from asap.economics.sla import BreachDetector, SLAMetrics
from asap.economics.sla_storage import SLAStorage
from asap.models.entities import (
    Capability,
    Endpoint,
    Manifest,
    Skill,
    SLADefinition,
)
from asap.models.envelope import Envelope
from asap.models.ids import generate_id
from asap.models.payloads import TaskRequest
from asap.transport.handlers import HandlerRegistry, create_echo_handler
from asap.transport.server import create_app
from asap.transport.jsonrpc import ASAP_METHOD
from asap.transport.ws import (
    SLA_BREACH_NOTIFICATION_METHOD,
    SLA_SUBSCRIBE_METHOD,
    broadcast_sla_breach,
)

# Constants
AGENT_A_URN = "urn:asap:agent:delegator"
AGENT_B_URN = "urn:asap:agent:delegate"
SHOWCASE_PORT = 8002
BASE_URL = f"http://127.0.0.1:{SHOWCASE_PORT}"
WS_URL = f"ws://127.0.0.1:{SHOWCASE_PORT}/asap/ws"
READY_TIMEOUT_SECONDS = 15.0
READY_POLL_INTERVAL_SECONDS = 0.3
SLOW_HANDLER_DELAY_SECONDS = 1.5  # Exceeds SLA max_latency_p95_ms=500

# Shared OAuth2 key (same process: server thread + client share this)
_OAUTH2_KEY_SET: jwk.KeySet | None = None
_OAUTH2_PRIVATE_KEY: jwk.RSAKey | None = None


def _log(msg: str) -> None:
    print(f"  {msg}")


def _log_step(step: int, title: str) -> None:
    print(f"\n--- Step {step}: {title} ---")


def _usage_metrics_to_sla_metrics(
    agent_id: str,
    events: list[UsageMetrics],
    period_start: datetime,
    period_end: datetime,
    uptime_percent: float = 100.0,
) -> SLAMetrics | None:
    if not events:
        return None
    durations = [e.duration_ms for e in events]
    tasks_completed = len(events)
    tasks_failed = 0
    return SLAMetrics(
        agent_id=agent_id,
        period_start=period_start,
        period_end=period_end,
        uptime_percent=uptime_percent,
        latency_p95_ms=compute_latency_p95_ms(durations),
        error_rate_percent=compute_error_rate_percent(tasks_completed, tasks_failed),
        tasks_completed=tasks_completed,
        tasks_failed=tasks_failed,
    )


def _make_bearer_token() -> str:
    global _OAUTH2_PRIVATE_KEY
    if _OAUTH2_PRIVATE_KEY is None:
        raise RuntimeError("OAuth2 key not initialized")
    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    claims = {
        "sub": AGENT_A_URN,
        "scope": "asap:execute",
        "exp": now + 3600,
    }
    return jose_jwt.encode(header, claims, _OAUTH2_PRIVATE_KEY)


def _create_showcase_app() -> Any:
    global _OAUTH2_KEY_SET
    if _OAUTH2_KEY_SET is None:
        raise RuntimeError("OAuth2 key set not initialized")

    manifest = Manifest(
        id=AGENT_B_URN,
        name="Delegate Agent (v1.3.0 Showcase)",
        version="1.3.0",
        description="Agent B: executes tasks, tracks usage, reports SLA",
        capabilities=Capability(
            asap_version="0.1",
            skills=[Skill(id="echo", description="Echo with optional delay")],
            state_persistence=False,
        ),
        endpoints=Endpoint(asap=f"{BASE_URL}/asap"),
        sla=SLADefinition(
            availability="99%",
            max_latency_p95_ms=500,
            max_error_rate="1%",
        ),
    )

    metering = InMemoryMeteringStorage()
    sla_storage = InMemorySLAStorage()
    delegation_storage = InMemoryDelegationStorage()
    private_key, _ = generate_keypair()

    async def jwks_fetcher(_uri: str) -> jwk.KeySet:
        return _OAUTH2_KEY_SET

    def delegation_key_store(_delegator_urn: str) -> Any:
        return private_key

    async def slow_echo_handler(envelope: Any, manifest: Any) -> Any:
        await asyncio.sleep(SLOW_HANDLER_DELAY_SECONDS)
        return create_echo_handler()(envelope, manifest)

    registry = HandlerRegistry()
    registry.register("task.request", slow_echo_handler)

    app = create_app(
        manifest,
        registry=registry,
        oauth2_config=OAuth2Config(
            jwks_uri="https://auth.example.com/jwks.json",
            path_prefix="/asap",
            jwks_fetcher=jwks_fetcher,
        ),
        delegation_key_store=delegation_key_store,
        delegation_storage=delegation_storage,
        metering_storage=metering,
        sla_storage=sla_storage,
        rate_limit="999999/minute",
    )

    # Demo-only endpoint: trigger SLA check from metering data and broadcast breaches
    @app.post("/_demo/trigger-sla-check")
    async def trigger_sla_check() -> dict[str, Any]:
        metering_storage = getattr(app.state, "metering_storage", None)
        sla_storage_obj = getattr(app.state, "sla_storage", None)
        manifest_obj = getattr(app.state, "manifest", None)
        subscribers = getattr(app.state, "sla_breach_subscribers", None) or set()

        if not metering_storage or not sla_storage_obj or not manifest_obj:
            return {"ok": False, "error": "Missing metering/sla/manifest"}

        now = datetime.now(timezone.utc)
        period_end = now
        period_start = now - timedelta(hours=1)
        query = MeteringQuery(
            agent_id=AGENT_B_URN,
            start=period_start,
            end=period_end,
            limit=1000,
        )
        events = await metering_storage.query(query)
        sla_metrics = _usage_metrics_to_sla_metrics(AGENT_B_URN, events, period_start, period_end)
        if not sla_metrics:
            return {"ok": True, "breaches": 0, "reason": "no_usage_data"}

        await sla_storage_obj.record_metrics(sla_metrics)
        detector = BreachDetector(
            storage=cast(SLAStorage, sla_storage_obj),
            on_breach=lambda b: broadcast_sla_breach(b, subscribers),
        )
        breaches = await detector.check_and_record(AGENT_B_URN, manifest_obj.sla, sla_metrics)
        return {
            "ok": True,
            "breaches": len(breaches),
            "latency_p95_ms": sla_metrics.latency_p95_ms,
        }

    return app


def wait_for_ready(url: str, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, timeout=1.0)
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(READY_POLL_INTERVAL_SECONDS)
    raise RuntimeError(f"Service not ready after {timeout_seconds:.1f}s: {url}")


async def run_showcase() -> None:
    bearer = _make_bearer_token()

    _log_step(1, "Delegation — Agent A creates token for Agent B (max_tasks=5)")
    create_resp = httpx.post(
        f"{BASE_URL}/asap/delegations",
        json={
            "delegate": AGENT_B_URN,
            "scopes": ["task.execute"],
            "max_tasks": 5,
        },
        headers={"Authorization": f"Bearer {bearer}"},
        timeout=10.0,
    )
    if create_resp.status_code != 201:
        raise RuntimeError(f"Delegation failed: {create_resp.status_code} {create_resp.text}")
    _log(f"Token created for {AGENT_B_URN} with max_tasks=5")

    _log_step(2, "Metering — Agent B performs tasks (slow handler simulates delay)")
    task_request = TaskRequest(
        conversation_id=generate_id(),
        skill_id="echo",
        input={"message": "Showcase task 1"},
    )
    envelope = Envelope(
        asap_version="0.1",
        sender=AGENT_A_URN,
        recipient=AGENT_B_URN,
        payload_type="task.request",
        payload=task_request.model_dump(),
        trace_id=generate_id(),
    )
    jsonrpc = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": ASAP_METHOD,
        "params": {"envelope": envelope.model_dump(mode="json")},
    }
    task_resp = httpx.post(
        f"{BASE_URL}/asap",
        json=jsonrpc,
        headers={"Authorization": f"Bearer {bearer}"},
        timeout=30.0,
    )
    if task_resp.status_code != 200:
        raise RuntimeError(f"Task failed: {task_resp.status_code} {task_resp.text}")
    _log("Task executed (handler slept 1.5s -> high latency recorded)")

    _log_step(3, "Transparency — Agent A queries GET /usage")
    usage_resp = httpx.get(f"{BASE_URL}/usage", timeout=5.0)
    if usage_resp.status_code != 200:
        raise RuntimeError(f"Usage API failed: {usage_resp.status_code}")
    usage_records = usage_resp.json().get("data", [])
    _log(f"Usage records: {len(usage_records)} task(s) from Agent B")
    for record in usage_records[:3]:
        _log(f"  - task_id={record.get('task_id', '?')} duration_ms={record.get('duration_ms', 0)}")

    _log_step(4, "Trust/SLA — Subscribe to WebSocket, trigger SLA check, receive breach")
    breach_received: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()

    async def ws_listener(ws: Any) -> None:
        async for msg in ws:
            if not isinstance(msg, str):
                continue
            try:
                payload = json.loads(msg)
            except json.JSONDecodeError:
                continue
            if payload.get("method") == SLA_BREACH_NOTIFICATION_METHOD:
                breach = payload.get("params", {}).get("breach", {})
                if not breach_received.done():
                    breach_received.set_result(breach)

    async with ws_connect(WS_URL) as ws:
        await ws.send(json.dumps({"jsonrpc": "2.0", "method": SLA_SUBSCRIBE_METHOD, "id": 1}))
        recv = await asyncio.wait_for(ws.recv(), timeout=5.0)
        sub_result = json.loads(recv)
        if sub_result.get("result", {}).get("subscribed") is not True:
            raise RuntimeError("WebSocket sla.subscribe failed")

        _log("Subscribed to sla.breach notifications")

        trigger_resp = httpx.post(f"{BASE_URL}/_demo/trigger-sla-check", timeout=5.0)
        if trigger_resp.status_code != 200:
            raise RuntimeError(f"Trigger SLA check failed: {trigger_resp.status_code}")
        trigger_data = trigger_resp.json()
        _log(f"SLA check triggered: {trigger_data.get('breaches', 0)} breach(es) detected")

        listener_task = asyncio.create_task(ws_listener(ws))
        try:
            breach = await asyncio.wait_for(breach_received, timeout=5.0)
            _log(
                f"BREACH ALERT received: {breach.get('breach_type')} — "
                f"threshold={breach.get('threshold')} actual={breach.get('actual')}"
            )
        except asyncio.TimeoutError:
            _log("(No breach notification within 5s — check trigger endpoint)")
        finally:
            listener_task.cancel()
            with suppress(asyncio.CancelledError):
                await listener_task

    print("\n✅ v1.3.0 Showcase complete!\n")


def main() -> None:
    global _OAUTH2_KEY_SET, _OAUTH2_PRIVATE_KEY

    print("\n🚀 ASAP v1.3.0 End-to-End Showcase\n")
    print("Demonstrating: Delegation → Metering → Transparency → SLA Breach Alert\n")

    key = jwk.RSAKey.generate_key(2048, private=True)
    _OAUTH2_KEY_SET = jwk.KeySet.import_key_set({"keys": [key.as_dict(private=False)]})
    _OAUTH2_PRIVATE_KEY = key

    app = _create_showcase_app()
    server_error: Exception | None = None

    def run_server() -> None:
        nonlocal server_error
        import uvicorn

        try:
            uvicorn.run(
                app,
                host="127.0.0.1",
                port=SHOWCASE_PORT,
                log_level="warning",
            )
        except Exception as e:
            server_error = e

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    try:
        wait_for_ready(f"{BASE_URL}/health", READY_TIMEOUT_SECONDS)
        _log("Agent B (delegate) server ready")
        asyncio.run(run_showcase())
    except Exception as e:
        print(f"\n❌ Showcase failed: {e}\n")
        raise
    finally:
        if server_error:
            print(f"Server error: {server_error}")


if __name__ == "__main__":
    main()
