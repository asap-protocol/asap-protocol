"""Offset-only usage queries must skip rows when no limit is given.

SQLite expresses that as ``LIMIT -1 OFFSET ?``. Dropping the branch returns
the skipped rows and corrupts usage pages.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from asap.economics.metering import UsageMetrics as EconomicsUsageMetrics
from asap.economics.storage import (
    InMemoryMeteringStorage,
    MeteringQuery,
    SQLiteMeteringStorage,
)
from asap.state.metering import InMemoryMeteringStore, UsageEvent, UsageMetrics
from asap.state.stores.sqlite import SQLiteMeteringStore

_START = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
_END = datetime(2026, 6, 15, 18, 0, 0, tzinfo=timezone.utc)
_AGENT_ID = "urn:asap:agent:offset-page"


def _stamp(index: int) -> datetime:
    return _START + timedelta(minutes=index)


async def _record_state_events(store: InMemoryMeteringStore | SQLiteMeteringStore) -> None:
    for index in range(5):
        await store.record(
            UsageEvent(
                task_id=f"t{index}",
                agent_id=_AGENT_ID,
                consumer_id="urn:asap:agent:consumer",
                metrics=UsageMetrics(),
                timestamp=_stamp(index),
            )
        )


async def _record_economics_events(
    store: InMemoryMeteringStorage | SQLiteMeteringStorage,
) -> None:
    for index in range(5):
        await store.record(
            EconomicsUsageMetrics(
                task_id=f"t{index}",
                agent_id=_AGENT_ID,
                consumer_id="urn:asap:agent:consumer",
                timestamp=_stamp(index),
            )
        )


def _assert_skipped(task_ids: list[str]) -> None:
    assert task_ids == ["t2", "t3", "t4"]


@pytest.mark.asyncio
async def test_state_metering_offset_without_limit_skips_earlier_rows(
    tmp_path: Path,
) -> None:
    """Both state stores keep rows from the offset through the end."""
    memory = InMemoryMeteringStore()
    sqlite = SQLiteMeteringStore(db_path=tmp_path / "state-usage.db")
    await _record_state_events(memory)
    await _record_state_events(sqlite)

    memory_page = await memory.query(_AGENT_ID, _START, _END, offset=2)
    sqlite_page = await sqlite.query(_AGENT_ID, _START, _END, offset=2)
    memory_tail = await memory.query(_AGENT_ID, _START, _END, offset=5)
    sqlite_tail = await sqlite.query(_AGENT_ID, _START, _END, offset=5)

    _assert_skipped([event.task_id for event in memory_page])
    _assert_skipped([event.task_id for event in sqlite_page])
    assert memory_tail == []
    assert sqlite_tail == []


@pytest.mark.asyncio
async def test_sqlite_state_metering_rejects_negative_offset(tmp_path: Path) -> None:
    """The SQLite state store uses the same non-negative offset guard as memory."""
    store = SQLiteMeteringStore(db_path=tmp_path / "state-usage.db")
    with pytest.raises(ValueError, match="offset must be non-negative"):
        await store.query(_AGENT_ID, _START, _END, offset=-1)


@pytest.mark.asyncio
async def test_economics_metering_offset_without_limit_skips_earlier_rows(
    tmp_path: Path,
) -> None:
    """Economics storage uses LIMIT -1 when limit is omitted; pages must still skip."""
    memory = InMemoryMeteringStorage()
    sqlite = SQLiteMeteringStorage(db_path=tmp_path / "economics-usage.db")
    await _record_economics_events(memory)
    await _record_economics_events(sqlite)
    page = MeteringQuery(offset=2)
    tail = MeteringQuery(offset=5)

    memory_page = await memory.query(page)
    sqlite_page = await sqlite.query(page)
    memory_tail = await memory.query(tail)
    sqlite_tail = await sqlite.query(tail)

    _assert_skipped([row.task_id for row in memory_page])
    _assert_skipped([row.task_id for row in sqlite_page])
    assert memory_tail == []
    assert sqlite_tail == []
