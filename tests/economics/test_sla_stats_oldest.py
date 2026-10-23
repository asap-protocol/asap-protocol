"""Lock which instant SLA ``stats()`` reports as the oldest record.

Retention uses this timestamp. In-memory stats take the minimum of every
metric ``period_start``, ``period_end``, and breach ``detected_at``. SQLite
takes the earlier of ``MIN(period_start)`` and ``MIN(detected_at)``. With an
ordered window (``period_end`` after ``period_start``) both backends agree:
the oldest instant wins, and an empty table does not.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from asap.economics.sla import SLABreach, SLAMetrics
from asap.economics.sla_storage import InMemorySLAStorage, SQLiteSLAStorage

_FEBRUARY = datetime(2026, 2, 1, 0, 0, 0, 500000, tzinfo=timezone.utc)
_MARCH = datetime(2026, 3, 1, 0, 0, 0, 500000, tzinfo=timezone.utc)
_JUNE = datetime(2026, 6, 15, 12, 0, 0, 500000, tzinfo=timezone.utc)
_AUGUST = datetime(2026, 8, 1, 0, 0, 0, 500000, tzinfo=timezone.utc)
_HOUR = timedelta(hours=1)


def _open_store(backend: str, tmp_path: Path) -> InMemorySLAStorage | SQLiteSLAStorage:
    """Return a fresh SLA store for ``backend``."""
    if backend == "memory":
        return InMemorySLAStorage()
    if backend == "sqlite":
        return SQLiteSLAStorage(db_path=str(tmp_path / "sla-oldest.db"))
    raise ValueError(f"backend must be 'memory' or 'sqlite', got {backend!r}")


def _metric(period_start: datetime, period_end: datetime | None = None) -> SLAMetrics:
    """One metrics row. ``period_end`` defaults to one hour after the start."""
    return SLAMetrics(
        agent_id="urn:asap:agent:sla-oldest",
        period_start=period_start,
        period_end=period_start + _HOUR if period_end is None else period_end,
        uptime_percent=99.0,
        latency_p95_ms=10,
        error_rate_percent=0.1,
        tasks_completed=4,
        tasks_failed=0,
    )


def _breach(detected_at: datetime, breach_id: str = "breach-oldest") -> SLABreach:
    """One breach row detected at ``detected_at``."""
    return SLABreach(
        id=breach_id,
        agent_id="urn:asap:agent:sla-oldest",
        breach_type="latency",
        threshold="500ms",
        actual="900ms",
        severity="warning",
        detected_at=detected_at,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["memory", "sqlite"])
@pytest.mark.parametrize(
    ("metric_start", "breach_at", "expected"),
    [
        (_JUNE, _MARCH, _MARCH),
        (_MARCH, _AUGUST, _MARCH),
    ],
)
async def test_stats_oldest_is_earlier_of_period_start_and_breach(
    backend: str,
    metric_start: datetime,
    breach_at: datetime,
    expected: datetime,
    tmp_path: Path,
) -> None:
    """A later breach must not hide an older period, or the reverse."""
    store = _open_store(backend, tmp_path)
    await store.record_metrics(_metric(metric_start))
    await store.record_breach(_breach(breach_at))

    stats = await store.stats()

    assert stats.total_events == 2
    assert stats.oldest_timestamp == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_stats_oldest_is_earliest_period_start_not_insert_order(
    backend: str,
    tmp_path: Path,
) -> None:
    """Inserting the newer period first still leaves March as the oldest."""
    store = _open_store(backend, tmp_path)
    await store.record_metrics(_metric(_JUNE))
    await store.record_metrics(_metric(_MARCH))

    stats = await store.stats()

    assert stats.total_events == 2
    assert stats.oldest_timestamp == _MARCH


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["memory", "sqlite"])
@pytest.mark.parametrize(
    ("source", "expected"),
    [("metrics", _MARCH), ("breaches", _MARCH)],
)
async def test_stats_oldest_uses_the_only_populated_table(
    backend: str,
    source: str,
    expected: datetime,
    tmp_path: Path,
) -> None:
    """An empty metrics or breach table must not clear the other table's oldest."""
    store = _open_store(backend, tmp_path)
    if source == "metrics":
        await store.record_metrics(_metric(_MARCH))
    elif source == "breaches":
        await store.record_breach(_breach(_MARCH))
    else:
        raise ValueError(f"source must be 'metrics' or 'breaches', got {source!r}")

    stats = await store.stats()

    assert stats.total_events == 1
    assert stats.oldest_timestamp == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_stats_oldest_breach_is_min_detected_at(backend: str, tmp_path: Path) -> None:
    """Two breaches must keep the earlier ``detected_at``. ``MAX`` would return August."""
    store = _open_store(backend, tmp_path)
    await store.record_breach(_breach(_AUGUST, "breach-late"))
    await store.record_breach(_breach(_MARCH, "breach-early"))

    stats = await store.stats()

    assert stats.total_events == 2
    assert stats.oldest_timestamp == _MARCH


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_stats_oldest_period_end_counts_only_in_memory(backend: str, tmp_path: Path) -> None:
    """Memory mins ``period_end``. SQLite reads ``period_start`` and ``detected_at`` only.

    A later end cannot be the minimum, so this window ends before it starts.
    """
    store = _open_store(backend, tmp_path)
    await store.record_metrics(_metric(_MARCH, _FEBRUARY))

    stats = await store.stats()

    expected = _FEBRUARY if backend == "memory" else _MARCH
    assert stats.total_events == 1
    assert stats.oldest_timestamp == expected
