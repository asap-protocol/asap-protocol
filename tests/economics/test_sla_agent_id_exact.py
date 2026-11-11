"""SLA metric and breach queries must use the full agent id.

``urn:asap:agent:sla-01`` must not return ``sla-010``, ``sla-0``, a case
variant, or a padded copy. Memory compares with ``!=`` and SQLite binds
``agent_id = ?``, so both backends are recorded.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from asap.economics.sla import SLABreach, SLAMetrics
from asap.economics.sla_storage import InMemorySLAStorage, SQLiteSLAStorage

_EXACT = "urn:asap:agent:sla-01"
# Neighbors a containment, casefold, or strip comparison would include.
_NEIGHBORS: tuple[str, ...] = (
    "urn:asap:agent:sla-010",
    "urn:asap:agent:sla-0",
    "URN:asap:agent:sla-01",
    "urn:asap:agent:sla-01 ",
    " urn:asap:agent:sla-01",
)
_PERIOD_START = datetime(2026, 6, 1, tzinfo=timezone.utc)
_PERIOD_END = datetime(2026, 6, 2, tzinfo=timezone.utc)


def _open_sla_store(backend: str, tmp_path: Path) -> InMemorySLAStorage | SQLiteSLAStorage:
    """Return an empty SLA store for ``backend``."""
    if backend == "memory":
        return InMemorySLAStorage()
    if backend == "sqlite":
        return SQLiteSLAStorage(db_path=tmp_path / "sla-agent-exact.db")
    raise ValueError(f"backend must be 'memory' or 'sqlite', got {backend!r}")


def _metrics(agent_id: str) -> SLAMetrics:
    """One metrics row for ``agent_id``."""
    return SLAMetrics(
        agent_id=agent_id,
        period_start=_PERIOD_START,
        period_end=_PERIOD_END,
        uptime_percent=99.0,
        latency_p95_ms=100,
        error_rate_percent=1.0,
        tasks_completed=1,
        tasks_failed=0,
    )


def _breach(agent_id: str) -> SLABreach:
    """One breach row whose id is the agent id under test."""
    return SLABreach(
        id=agent_id,
        agent_id=agent_id,
        breach_type="latency",
        threshold="500ms",
        actual="600ms",
        severity="warning",
        detected_at=_PERIOD_END,
    )


async def _record_neighbor_rows(store: InMemorySLAStorage | SQLiteSLAStorage) -> None:
    """Store the exact agent beside prefix, case, and padded neighbors."""
    for agent_id in (_EXACT, *_NEIGHBORS):
        await store.record_metrics(_metrics(agent_id))
        await store.record_breach(_breach(agent_id))


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_sla_agent_id_query_is_exact(backend: str, tmp_path: Path) -> None:
    """Metrics, counts, and breaches stay on the exact agent id."""
    store = _open_sla_store(backend, tmp_path)
    await _record_neighbor_rows(store)

    metrics = await store.query_metrics(agent_id=_EXACT)
    breaches = await store.query_breaches(agent_id=_EXACT)
    longer = await store.query_metrics(agent_id="urn:asap:agent:sla-010")
    case_rows = await store.query_breaches(agent_id="URN:asap:agent:sla-01")

    assert {row.agent_id for row in metrics} == {_EXACT}
    assert await store.count_metrics(agent_id=_EXACT) == 1
    assert {row.agent_id for row in breaches} == {_EXACT}
    assert {row.agent_id for row in longer} == {"urn:asap:agent:sla-010"}
    assert {row.agent_id for row in case_rows} == {"URN:asap:agent:sla-01"}
