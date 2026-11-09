"""Append must reseal the hash chain and accept non-JSON detail values.

Callers can put ``prev_hash`` and ``hash`` on an ``AuditEntry``. Both stores
ignore those fields and recompute the link. Detail values that are not JSON
natives (a timezone-aware datetime) are hashed with ``default=str`` and still
verify after a SQLite round trip.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from asap.economics.audit import (
    AuditEntry,
    InMemoryAuditStore,
    SQLiteAuditStore,
    compute_entry_hash,
)

_TS = datetime(2026, 4, 2, 15, 4, tzinfo=timezone.utc)
_WHEN = datetime(2026, 1, 1, tzinfo=timezone.utc)
_URN = "urn:asap:agent:audit-reseal"
_DETAILS: dict[str, Any] = {"b": 1, "a": 2}
_CANONICAL_STAMP = "2026-04-02T15:04:00+00:00"
_CANONICAL_DETAILS = '{"a": 2, "b": 1}'


def _store(backend: str, tmp_path: Path) -> InMemoryAuditStore | SQLiteAuditStore:
    """Return a fresh audit store for ``backend``."""
    if backend == "memory":
        return InMemoryAuditStore()
    if backend == "sqlite":
        return SQLiteAuditStore(db_path=str(tmp_path / "audit-reseal.db"))
    raise ValueError(f"backend must be 'memory' or 'sqlite', got {backend!r}")


def _digest(canonical: str) -> str:
    """SHA-256 hex of a canonical audit string, independent of the helper."""
    return hashlib.sha256(canonical.encode()).hexdigest()


def _forged(operation: str, details: dict[str, Any] | None = None) -> AuditEntry:
    """Entry whose caller-supplied chain fields must not be stored."""
    return AuditEntry(
        timestamp=_TS,
        operation=operation,
        agent_urn=_URN,
        details=_DETAILS if details is None else details,
        prev_hash="forged-prev",
        hash="deadbeef",
    )


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_append_reseals_caller_supplied_chain_fields(backend: str, tmp_path: Path) -> None:
    """Forged prev_hash and hash are replaced, and the chain still verifies."""
    store = _store(backend, tmp_path)
    first = await store.append(_forged("task.request"))
    second = await store.append(_forged("task.complete"))

    assert first.prev_hash == ""
    assert first.hash != "deadbeef"
    first_canonical = f"|{_CANONICAL_STAMP}|task.request|{_CANONICAL_DETAILS}"
    assert first.hash == _digest(first_canonical)
    assert second.prev_hash == first.hash
    assert second.hash != "deadbeef"
    second_canonical = f"{first.hash}|{_CANONICAL_STAMP}|task.complete|{_CANONICAL_DETAILS}"
    assert second.hash == _digest(second_canonical)
    assert await store.verify_chain() is True


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_append_hashes_datetime_details(backend: str, tmp_path: Path) -> None:
    """A datetime detail value is canonicalized with ``default=str`` and verifies."""
    store = _store(backend, tmp_path)
    details = {"when": _WHEN}
    sealed = await store.append(_forged("task.request", details))

    assert sealed.hash == compute_entry_hash("", _TS, "task.request", details)
    assert await store.verify_chain() is True
    loaded = await store.query()
    assert len(loaded) == 1
    assert loaded[0].hash == sealed.hash
