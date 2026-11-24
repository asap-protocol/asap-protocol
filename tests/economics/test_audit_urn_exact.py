"""Audit queries must use the full agent URN.

A prefix, case, or padding match would return another actor's log. Memory
compares with ``==`` and SQLite binds ``agent_urn = ?``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from asap.economics.audit import AuditEntry, InMemoryAuditStore, SQLiteAuditStore

_EXACT = "urn:asap:agent:actor-01"
_NEIGHBORS: tuple[str, ...] = (
    "urn:asap:agent:actor-010",
    "urn:asap:agent:actor-0",
    "URN:asap:agent:actor-01",
    "urn:asap:agent:actor-01 ",
    " urn:asap:agent:actor-01",
)
_STAMP = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)


def _open_audit_store(backend: str, tmp_path: Path) -> InMemoryAuditStore | SQLiteAuditStore:
    """Return an empty audit store for ``backend``."""
    if backend == "memory":
        return InMemoryAuditStore()
    if backend == "sqlite":
        return SQLiteAuditStore(db_path=str(tmp_path / "audit-urn-exact.db"))
    raise ValueError(f"backend must be 'memory' or 'sqlite', got {backend!r}")


def _entry(agent_urn: str) -> AuditEntry:
    """One audit row for ``agent_urn``."""
    return AuditEntry(
        timestamp=_STAMP,
        operation="task.execute",
        agent_urn=agent_urn,
    )


async def _append_neighbor_rows(store: InMemoryAuditStore | SQLiteAuditStore) -> None:
    """Append the exact actor beside prefix, case, and padded neighbors."""
    for agent_urn in (_EXACT, *_NEIGHBORS):
        await store.append(_entry(agent_urn))


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_audit_agent_urn_query_is_exact(backend: str, tmp_path: Path) -> None:
    """Filtering by URN returns only that actor's row."""
    store = _open_audit_store(backend, tmp_path)
    await _append_neighbor_rows(store)

    exact = await store.query(agent_urn=_EXACT)
    case_neighbor = await store.query(agent_urn="URN:asap:agent:actor-01")
    longer = await store.query(agent_urn="urn:asap:agent:actor-010")

    assert {row.agent_urn for row in exact} == {_EXACT}
    assert {row.agent_urn for row in case_neighbor} == {"URN:asap:agent:actor-01"}
    assert {row.agent_urn for row in longer} == {"urn:asap:agent:actor-010"}
    assert await store.query(agent_urn="urn:asap:agent:actor") == []
