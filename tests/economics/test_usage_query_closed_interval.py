"""Usage queries include both window endpoints and drop the next microsecond.

Coarser fixtures that query a multi-hour span still pass if ``>=`` / ``<=``
become exclusive. Economics storage and the SQLite state store both compare
timestamps, the latter as ISO text.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import pytest

from asap.economics.metering import UsageMetrics
from asap.economics.storage import (
    InMemoryMeteringStorage,
    MeteringQuery,
    MeteringStorage,
    SQLiteMeteringStorage,
)
from asap.state.metering import UsageEvent
from asap.state.metering import UsageMetrics as StateUsageMetrics
from asap.state.stores.sqlite import SQLiteMeteringStore

_START = datetime(2026, 6, 15, 11, 0, tzinfo=timezone.utc)
_END = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
_ONE_MICROSECOND = timedelta(microseconds=1)
_AGENT_ID = "urn:asap:agent:billing-window"
_CONSUMER_ID = "urn:asap:agent:consumer"
_BackendName = Literal["memory", "sqlite"]

_SAMPLES = (
    ("before-start", _START - _ONE_MICROSECOND),
    ("at-start", _START),
    ("at-end", _END),
    ("after-end", _END + _ONE_MICROSECOND),
)


def _economics_row(task_id: str, when: datetime) -> UsageMetrics:
    """One billing row at ``when``."""
    return UsageMetrics(
        task_id=task_id,
        agent_id=_AGENT_ID,
        consumer_id=_CONSUMER_ID,
        tokens_in=1,
        timestamp=when,
    )


def _state_event(task_id: str, when: datetime) -> UsageEvent:
    """One state usage event at ``when``."""
    return UsageEvent(
        task_id=task_id,
        agent_id=_AGENT_ID,
        consumer_id=_CONSUMER_ID,
        metrics=StateUsageMetrics(tokens_in=1),
        timestamp=when,
    )


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_economics_query_includes_both_endpoints(
    backend: _BackendName,
    tmp_path: Path,
) -> None:
    """In-memory and SQLite economics queries keep the endpoints and drop ±1µs."""
    storage: MeteringStorage
    if backend == "memory":
        storage = InMemoryMeteringStorage()
    else:
        storage = SQLiteMeteringStorage(db_path=tmp_path / "economics-usage.db")

    for task_id, when in _SAMPLES:
        await storage.record(_economics_row(task_id, when))

    found = await storage.query(MeteringQuery(agent_id=_AGENT_ID, start=_START, end=_END))
    assert [row.task_id for row in found] == ["at-start", "at-end"]


async def test_sqlite_metering_store_query_includes_both_endpoints(tmp_path: Path) -> None:
    """State SQLite text comparison keeps both endpoints and drops ±1µs."""
    store = SQLiteMeteringStore(db_path=tmp_path / "state-usage.db")
    for task_id, when in _SAMPLES:
        await store.record(_state_event(task_id, when))

    found = await store.query(_AGENT_ID, _START, _END)
    assert [event.task_id for event in found] == ["at-start", "at-end"]
