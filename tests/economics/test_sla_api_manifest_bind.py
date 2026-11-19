"""SLA HTTP checks must use the whole agent id and the whole severity token.

``GET /sla`` applies a manifest contract only when ``manifest.id == agent_id``.
``GET /sla/breaches`` rejects a severity token before filtering. A prefix, case
change, or padded copy must not inherit the contract or pass the allow-list.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi.testclient import TestClient

from asap.economics import InMemorySLAStorage, SLABreach, SLAMetrics
from asap.models.entities import Capability, Endpoint, Manifest, Skill, SLADefinition
from asap.transport.server import create_app

_EXACT = "urn:asap:agent:sla-bind-01"
_NEIGHBORS: tuple[str, ...] = (
    "urn:asap:agent:sla-bind-010",
    "urn:asap:agent:sla-bind-0",
    "URN:asap:agent:sla-bind-01",
    "urn:asap:agent:sla-bind-01 ",
    " urn:asap:agent:sla-bind-01",
)
_SEVERITY_DETAIL = "severity must be one of: warning, critical"
_SEVERITY_NEIGHBORS: tuple[str, ...] = (
    "warn",
    "warnings",
    "Warning",
    " warning",
    "crit",
    "critical ",
)


def _manifest() -> Manifest:
    """Manifest whose SLA contract belongs only to ``_EXACT``."""
    return Manifest(
        id=_EXACT,
        name="SLA bind",
        version="1.0.0",
        description="SLA manifest binding",
        capabilities=Capability(
            asap_version="0.1",
            skills=[Skill(id="echo", description="Echo")],
            state_persistence=False,
        ),
        endpoints=Endpoint(asap="http://localhost:8000/asap"),
        sla=SLADefinition(
            availability="99%",
            max_latency_p95_ms=500,
            max_error_rate="1%",
        ),
    )


def _healthy(agent_id: str) -> SLAMetrics:
    """Metrics inside the default 24h window and inside the manifest SLA."""
    now = datetime.now(timezone.utc)
    return SLAMetrics(
        agent_id=agent_id,
        period_start=now - timedelta(hours=1),
        period_end=now,
        uptime_percent=99.5,
        latency_p95_ms=120,
        error_rate_percent=0.5,
        tasks_completed=100,
        tasks_failed=1,
    )


def _client(storage: InMemorySLAStorage) -> TestClient:
    """SLA API client with the shared test rate-limit ceiling."""
    app = create_app(_manifest(), sla_storage=storage, rate_limit="999999/minute")
    return TestClient(app)


def test_sla_compliance_uses_the_exact_manifest_id() -> None:
    """Only the manifest id gets a compliance score. Lookalike rows stay unbound."""
    storage = InMemorySLAStorage()
    for agent_id in (_EXACT, *_NEIGHBORS):
        asyncio.run(storage.record_metrics(_healthy(agent_id)))

    response = _client(storage).get("/sla")
    assert response.status_code == 200
    rows = response.json()["data"]
    by_agent = {row["agent_id"]: row["compliance_percent"] for row in rows}

    assert set(by_agent) == {_EXACT, *_NEIGHBORS}
    assert by_agent[_EXACT] == 100.0
    for neighbor in _NEIGHBORS:
        assert by_agent[neighbor] is None


def test_sla_breach_severity_token_is_exact() -> None:
    """``warning`` returns that row. A lookalike token is rejected before filtering."""
    storage = InMemorySLAStorage()
    detected = datetime.now(timezone.utc)
    for breach_id, severity in (("breach-warn", "warning"), ("breach-crit", "critical")):
        asyncio.run(
            storage.record_breach(
                SLABreach(
                    id=breach_id,
                    agent_id=_EXACT,
                    breach_type="latency",
                    threshold="500ms",
                    actual="600ms",
                    severity=severity,
                    detected_at=detected,
                )
            )
        )
    client = _client(storage)

    exact = client.get("/sla/breaches", params={"severity": "warning"})
    assert exact.status_code == 200
    body: dict[str, Any] = exact.json()
    assert [row["id"] for row in body["data"]] == ["breach-warn"]

    for severity in _SEVERITY_NEIGHBORS:
        rejected = client.get("/sla/breaches", params={"severity": severity})
        assert rejected.status_code == 400
        assert rejected.json()["detail"] == _SEVERITY_DETAIL
