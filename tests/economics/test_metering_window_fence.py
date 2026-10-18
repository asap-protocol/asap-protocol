"""Lock inclusive metering windows and the retention cutoff second.

Rolling hour/week and calendar-day aggregates keep a sample on the opening
instant and on ``now``, and drop the microsecond outside each side. Retention
purge floors the cutoff to the whole second and keeps that instant. Coarser
tests (two hours, eight days, a two-hour TTL gap) still pass if those
comparisons flip.
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
from asap.state.metering import (
    InMemoryMeteringStore,
    MeteringStorageBridge,
    UsageEvent,
)
from asap.state.metering import UsageMetrics as StateUsageMetrics

_FROZEN_NOW = datetime(2026, 6, 15, 12, 0, 0, 500000, tzinfo=timezone.utc)
_ONE_MICROSECOND = timedelta(microseconds=1)
_AGENT_ID = "urn:asap:agent:billing"
_CONSUMER_ID = "urn:asap:agent:consumer"
_BackendName = Literal["memory", "sqlite"]


class _FrozenUtcNow(datetime):
    """``datetime`` stand-in whose ``now()`` is :data:`_FROZEN_NOW`."""

    @classmethod
    def now(cls, tz: timezone | None = None) -> _FrozenUtcNow:
        """Return the frozen instant, converted when ``tz`` is set."""
        source = _FROZEN_NOW.replace(tzinfo=None) if tz is None else _FROZEN_NOW.astimezone(tz)
        return cls(
            source.year,
            source.month,
            source.day,
            source.hour,
            source.minute,
            source.second,
            source.microsecond,
            tzinfo=source.tzinfo,
        )


def _usage(task_id: str, tokens_in: int, when: datetime) -> UsageMetrics:
    """Build one billing row with a single token count at ``when``."""
    return UsageMetrics(
        task_id=task_id,
        agent_id=_AGENT_ID,
        consumer_id=_CONSUMER_ID,
        tokens_in=tokens_in,
        timestamp=when,
    )


def _storage(
    backend: _BackendName,
    db_path: Path,
    *,
    retention_ttl_seconds: int | None,
) -> MeteringStorage:
    """Return an isolated in-memory or SQLite metering backend."""
    if backend == "memory":
        return InMemoryMeteringStorage(retention_ttl_seconds=retention_ttl_seconds)
    return SQLiteMeteringStorage(
        db_path=db_path,
        retention_ttl_seconds=retention_ttl_seconds,
    )


def _install_frozen_clock(monkeypatch: pytest.MonkeyPatch, dotted_name: str) -> None:
    """Replace ``datetime`` at ``dotted_name`` so ``now()`` stays at the fence."""
    monkeypatch.setattr(dotted_name, _FrozenUtcNow)


async def _assert_window(
    storage: MeteringStorage,
    *,
    period: str,
    opening: datetime,
) -> None:
    """Aggregate ``period`` and require both inclusive edges, nothing outside."""
    await storage.record(_usage("before-open", 100, opening - _ONE_MICROSECOND))
    await storage.record(_usage("at-open", 1, opening))
    await storage.record(_usage("at-now", 2, _FROZEN_NOW))
    await storage.record(_usage("after-now", 200, _FROZEN_NOW + _ONE_MICROSECOND))

    aggregate = await MeteringStorageBridge(storage).aggregate(_AGENT_ID, period)

    assert aggregate.period == period
    assert aggregate.total_tokens == 3
    assert aggregate.total_tasks == 2
    assert aggregate.total_api_calls == 0


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
@pytest.mark.parametrize("period", ["hour", "h"])
async def test_rolling_hour_includes_exact_edges(
    backend: _BackendName,
    period: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An event exactly one hour old counts; one microsecond older does not."""
    _install_frozen_clock(monkeypatch, "asap.state.metering.datetime")
    storage = _storage(backend, tmp_path / f"hour-{period}.db", retention_ttl_seconds=None)
    await _assert_window(
        storage,
        period=period,
        opening=_FROZEN_NOW - timedelta(hours=1),
    )


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
@pytest.mark.parametrize("period", ["day", "d", "today"])
async def test_calendar_day_includes_utc_midnight(
    backend: _BackendName,
    period: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``today`` starts at UTC midnight, including that instant."""
    _install_frozen_clock(monkeypatch, "asap.state.metering.datetime")
    storage = _storage(backend, tmp_path / f"day-{period}.db", retention_ttl_seconds=None)
    midnight = _FROZEN_NOW.replace(hour=0, minute=0, second=0, microsecond=0)
    await _assert_window(storage, period=period, opening=midnight)


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
@pytest.mark.parametrize("period", ["week", "w"])
async def test_rolling_week_includes_exact_edges(
    backend: _BackendName,
    period: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An event exactly seven days old counts; one microsecond older does not."""
    _install_frozen_clock(monkeypatch, "asap.state.metering.datetime")
    storage = _storage(backend, tmp_path / f"week-{period}.db", retention_ttl_seconds=None)
    await _assert_window(
        storage,
        period=period,
        opening=_FROZEN_NOW - timedelta(weeks=1),
    )


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_purge_keeps_floored_cutoff_second(
    backend: _BackendName,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retention drops the microsecond before the floored cutoff and keeps that second.

    ``now`` carries 500000 microseconds so a purge that skips
    ``replace(microsecond=0)`` deletes the cutoff-second row.
    """
    _install_frozen_clock(monkeypatch, "asap.economics.storage.datetime")
    storage = _storage(backend, tmp_path / "purge.db", retention_ttl_seconds=3600)
    floored_cutoff = (_FROZEN_NOW - timedelta(seconds=3600)).replace(microsecond=0)
    await storage.record(_usage("before-cutoff", 100, floored_cutoff - _ONE_MICROSECOND))
    await storage.record(_usage("at-cutoff", 1, floored_cutoff))
    await storage.record(_usage("fresh", 2, _FROZEN_NOW))

    removed = await storage.purge_expired()
    remaining = await storage.query(MeteringQuery())

    assert removed == 1
    assert sorted(row.task_id for row in remaining) == ["at-cutoff", "fresh"]


async def test_in_memory_metering_store_query_is_closed_interval() -> None:
    """State-store queries include both endpoints and exclude the adjacent microseconds."""
    start = datetime(2026, 6, 15, 11, 0, tzinfo=timezone.utc)
    end = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
    store = InMemoryMeteringStore()
    samples = (
        ("before-start", start - _ONE_MICROSECOND),
        ("at-start", start),
        ("at-end", end),
        ("after-end", end + _ONE_MICROSECOND),
    )
    for task_id, when in samples:
        await store.record(
            UsageEvent(
                task_id=task_id,
                agent_id=_AGENT_ID,
                consumer_id=_CONSUMER_ID,
                metrics=StateUsageMetrics(tokens_in=1),
                timestamp=when,
            )
        )

    found = await store.query(_AGENT_ID, start, end)

    assert [event.task_id for event in found] == ["at-start", "at-end"]
