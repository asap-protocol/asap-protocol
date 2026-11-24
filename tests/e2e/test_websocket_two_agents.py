"""End-to-end WebSocket round-trip regression gate.

Spins up a real uvicorn server exposing ``/asap/ws`` and drives a true
client↔server WebSocket round-trip: a client opens the WS handshake carrying a
Bearer token (``manifest.auth`` + ``token_validator``), sends a JSON-RPC
``task.request`` envelope for the ``echo`` skill, and asserts the server
returns a ``task.response`` frame whose ``correlation_id`` matches the request
``id`` and whose payload echoes the input back.

This regression gate protects the WS request/response flow during transport
refactors. ``tests/transport/test_websocket.py`` covers framing/unit concerns;
``tests/e2e/test_two_agents.py`` is HTTP-only. This test pins the full
client↔server WS path with auth + correlation exercised end-to-end.

A single-client round-trip is sufficient and intentionally chosen over a
two-agent flow: the gate's purpose is "WS client↔server round-trip works
end-to-end with auth + correlation", which a single client→echo-server request
already proves. The two-agent variant would add a coordinator hop without
exercising any additional WS code path.
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

import pytest
from websockets.asyncio.client import connect as ws_connect

from asap.models.entities import AuthScheme, Capability, Endpoint, Manifest, Skill
from asap.models.envelope import Envelope
from asap.models.payloads import TaskRequest
from asap.testing import assert_response_correlates
from asap.transport.handlers import create_default_registry
from asap.transport.jsonrpc import ASAP_METHOD, JsonRpcRequest
from asap.transport.server import create_app
from asap.transport.ws import decode_frame_to_json

from tests.transport.conftest import TEST_RATE_LIMIT_DEFAULT, NoRateLimitTestBase

# Bearer token accepted by the test ``token_validator`` and the agent identity
# it resolves to. The envelope ``sender`` MUST match this identity or the
# server's sender/auth binding rejects the request.
E2E_WS_TOKEN = "e2e-ws-bearer-token"
E2E_WS_CLIENT_ID = "urn:asap:agent:e2e-ws-client"
E2E_WS_SERVER_ID = "urn:asap:agent:e2e-ws-server"
E2E_WS_ECHO_INPUT = {"msg": "e2e-ws-correlation"}


def _free_port() -> int:
    """Return an unused TCP port on 127.0.0.1 for the ephemeral uvicorn server."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _auth_manifest() -> Manifest:
    """Manifest with a ``bearer`` auth scheme so the WS path enforces token validation."""
    return Manifest(
        id=E2E_WS_SERVER_ID,
        name="E2E WS Server",
        version="1.0.0",
        description="Echo server for the E2E WebSocket round-trip gate",
        capabilities=Capability(
            asap_version="0.1",
            skills=[Skill(id="echo", description="Echo input as output")],
            state_persistence=False,
        ),
        endpoints=Endpoint(asap="http://localhost:8000/asap"),
        auth=AuthScheme(schemes=["bearer"]),
    )


def _token_validator(token: str) -> str | None:
    """Validate a Bearer token and return the authenticated agent identity.

    Returns ``None`` for unknown tokens so the server's auth middleware rejects
    them with a JSON-RPC auth error, exercising the fail path end-to-end.
    """
    if token == E2E_WS_TOKEN:
        return E2E_WS_CLIENT_ID
    return None


def _task_request_frame(envelope: Envelope, request_id: str | int) -> str:
    """Serialize a ``task.request`` envelope into a JSON-RPC text frame."""
    rpc = JsonRpcRequest(
        method=ASAP_METHOD,
        params={"envelope": envelope.model_dump(mode="json")},
        id=request_id,
    )
    return json.dumps(rpc.model_dump())


class TestE2EWebSocketRoundTrip(NoRateLimitTestBase):
    """True client↔server E2E WebSocket round-trip with auth + correlation."""

    @pytest.mark.asyncio
    async def test_task_request_round_trips_over_websocket_with_auth(
        self,
        disable_rate_limiting: Any,
    ) -> None:
        """A Bearer-authenticated WS client sends ``task.request`` and receives a correlated ``task.response``.

        Steps:
        1. Build an auth-gated echo app (``manifest.auth`` bearer + ``token_validator``).
        2. Start a real uvicorn server on a free port in a daemon thread.
        3. Open the WS handshake with ``Authorization: Bearer <token>`` so the
           fake-request auth path validates the token via ``token_validator``.
        4. Send a JSON-RPC ``task.request`` frame for the ``echo`` skill.
        5. Receive the ``task.response`` frame and assert:
           - ``payload_type == "task.response"``
           - ``assert_response_correlates(request, response)`` (correlation_id == request id)
           - echoed payload: ``result["echoed"]`` contains the request input
           - the connection closes cleanly after.
        """
        import uvicorn
        from asap.transport import middleware as middleware_module

        # Isolate rate limiting: replace the module-level limiter AND the
        # app.state limiter so neither HTTP nor WS token-bucket paths can 429.
        middleware_module.limiter = disable_rate_limiting

        app_instance = create_app(
            _auth_manifest(),
            registry=create_default_registry(),
            token_validator=_token_validator,
            rate_limit=TEST_RATE_LIMIT_DEFAULT,
        )
        app_instance.state.limiter = disable_rate_limiting

        port = _free_port()
        server_started = threading.Event()
        thread_err: list[Exception] = []

        def run() -> None:
            try:
                config = uvicorn.Config(
                    app_instance,
                    host="127.0.0.1",
                    port=port,
                    log_level="warning",
                )
                server_started.set()
                asyncio.run(uvicorn.Server(config).serve())
            except Exception as exc:  # pragma: no cover - surfaced via thread_err
                thread_err.append(exc)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        assert server_started.wait(timeout=5.0)
        # Let the socket settle before the client connects.
        await asyncio.sleep(0.2)

        ws_url = f"ws://127.0.0.1:{port}/asap/ws"
        request_envelope = Envelope(
            asap_version="0.1",
            sender=E2E_WS_CLIENT_ID,
            recipient=E2E_WS_SERVER_ID,
            payload_type="task.request",
            payload=TaskRequest(
                conversation_id="e2e-ws-conv-1",
                skill_id="echo",
                input=E2E_WS_ECHO_INPUT,
            ).model_dump(),
        )
        # request_id is generated locally so the server-side envelope keeps its
        # own auto-generated id; correlation is asserted via the response's
        # ``correlation_id`` matching the request envelope ``id``.
        request_frame = _task_request_frame(request_envelope, request_id="e2e-ws-req-1")

        try:
            async with ws_connect(
                ws_url,
                additional_headers={"Authorization": f"Bearer {E2E_WS_TOKEN}"},
                open_timeout=10.0,
                close_timeout=5.0,
            ) as websocket:
                await websocket.send(request_frame)
                response_text = await asyncio.wait_for(websocket.recv(), timeout=10.0)
                # Clean close initiated by the client after the round-trip.
                await websocket.close()

            if thread_err:
                raise thread_err[0]

            assert isinstance(response_text, str)
            data = decode_frame_to_json(response_text)
            # JSON-RPC success envelope, not an error frame.
            assert data.get("jsonrpc") == "2.0"
            assert "error" not in data, f"unexpected JSON-RPC error: {data.get('error')}"
            assert "result" in data, f"missing result in WS frame: {data}"

            response_env_dict = data["result"].get("envelope")
            assert isinstance(response_env_dict, dict), (
                f"expected envelope dict in result, got {response_env_dict!r}"
            )
            response_envelope = Envelope.model_validate(response_env_dict)

            assert response_envelope.payload_type == "task.response"
            assert_response_correlates(request_envelope, response_envelope)

            echoed = response_envelope.payload_dict.get("result", {}).get("echoed")
            assert echoed == E2E_WS_ECHO_INPUT, (
                f"echoed payload mismatch: expected {E2E_WS_ECHO_INPUT!r}, got {echoed!r}"
            )
        except Exception as e:
            if thread_err:
                raise thread_err[0] from e
            raise
