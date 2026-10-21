"""Lock inclusive audit-log query endpoints on both stores."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from asap.economics.audit import AuditEntry, InMemoryAuditStore, SQLiteAuditStore

# Fractional seconds stay on every timestamp so SQLite ISO text order matches
# chronological order. ``datetime.isoformat`` omits a zero microsecond field.
_ANCHOR = datetime(2026, 6, 15, 12, 0, 0, 500000, tzinfo=timezone.utc)
_WINDOW_END = _ANCHOR + timedelta(seconds=1)
_MICROSECOND = timedelta(microseconds=1)


def _open_audit_store(backend: str, tmp_path: Path) -> InMemoryAuditStore | SQLiteAuditStore:
    """Return a fresh audit store for ``backend``."""
    if backend == "memory":
        return InMemoryAuditStore()
    if backend == "sqlite":
        return SQLiteAuditStore(db_path=str(tmp_path / "audit-fence.db"))
    raise ValueError(f"backend must be 'memory' or 'sqlite', got {backend!r}")


def _entry(operation: str, timestamp: datetime) -> AuditEntry:
    """Build one audit row identified by ``operation``."""
    return AuditEntry(
        timestamp=timestamp,
        operation=operation,
        agent_urn="urn:asap:agent:audit-fence",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_audit_query_includes_both_endpoints(backend: str, tmp_path: Path) -> None:
    """``start`` and ``end`` are inclusive; one microsecond outside is dropped.

    In-memory keeps ``timestamp >= start`` and ``timestamp <= end``. SQLite
    binds the same comparisons against ISO-8601 text.
    """
    store = _open_audit_store(backend, tmp_path)
    samples = (
        ("before", _ANCHOR - _MICROSECOND),
        ("start", _ANCHOR),
        ("end", _WINDOW_END),
        ("after", _WINDOW_END + _MICROSECOND),
    )
    for operation, timestamp in samples:
        await store.append(_entry(operation, timestamp))

    found = await store.query(start=_ANCHOR, end=_WINDOW_END)

    assert {row.operation for row in found} == {"start", "end"}
