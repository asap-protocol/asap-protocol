"""Usage queries keep task, agent, and consumer ids exact on both stores.

``task_01`` must not return ``task_010``, ``task_0``, a case variant, or a
padded copy. The in-memory ``==`` check and the SQLite ``= ?`` fragment are
separate, so both backends are recorded.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from asap.economics.metering import UsageMetrics
from asap.economics.storage import (
    InMemoryMeteringStorage,
    MeteringQuery,
    SQLiteMeteringStorage,
)

_STAMP = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
_EXACT = ("task_01", "agent_01", "consumer_01")
# Neighbors that a containment, casefold, or strip comparison would include.
_NEIGHBORS: tuple[tuple[str, str, str], ...] = (
    ("task_010", "agent_010", "consumer_010"),
    ("task_0", "agent_0", "consumer_0"),
    ("Task_01", "Agent_01", "Consumer_01"),
    ("task_01 ", "agent_01 ", "consumer_01 "),
    (" task_01", " agent_01", " consumer_01"),
)


def _open_store(backend: str, tmp_path: Path) -> InMemoryMeteringStorage | SQLiteMeteringStorage:
    """Return an empty usage store for ``backend``."""
    if backend == "memory":
        return InMemoryMeteringStorage()
    if backend == "sqlite":
        return SQLiteMeteringStorage(db_path=tmp_path / "usage-id-exact.db")
    raise ValueError(f"backend must be 'memory' or 'sqlite', got {backend!r}")


def _metrics(task_id: str, agent_id: str, consumer_id: str) -> UsageMetrics:
    """One usage row identified by the three ids under test."""
    return UsageMetrics(
        task_id=task_id,
        agent_id=agent_id,
        consumer_id=consumer_id,
        timestamp=_STAMP,
    )


async def _record_rows(store: InMemoryMeteringStorage | SQLiteMeteringStorage) -> None:
    """Store the exact row and every neighbor."""
    await store.record(_metrics(*_EXACT))
    for row in _NEIGHBORS:
        await store.record(_metrics(*row))


def _ids(rows: list[UsageMetrics], field: str) -> set[str]:
    """Collect one id field from a query result."""
    return {str(getattr(row, field)) for row in rows}


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_usage_query_ids_are_exact(backend: str, tmp_path: Path) -> None:
    """Each id filter returns the one exact row and drops neighbors."""
    store = _open_store(backend, tmp_path)
    await _record_rows(store)

    by_task = await store.query(MeteringQuery(task_id="task_01"))
    by_agent = await store.query(MeteringQuery(agent_id="agent_01"))
    by_consumer = await store.query(MeteringQuery(consumer_id="consumer_01"))

    assert _ids(by_task, "task_id") == {"task_01"}
    assert _ids(by_agent, "agent_id") == {"agent_01"}
    assert _ids(by_consumer, "consumer_id") == {"consumer_01"}
