"""Lock SLA overlap queries and the error-rate severity floor."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from asap.economics.sla import SLABreach, SLAMetrics, evaluate_breach_conditions
from asap.economics.sla_storage import InMemorySLAStorage, SQLiteSLAStorage
from asap.models.entities import SLADefinition

# Every instant below carries microseconds. SQLite compares ISO text, and a
# zero-microsecond value is formatted without a fractional part, which sorts
# differently from a fractional neighbour in the same second.
_ANCHOR = datetime(2026, 6, 15, 12, 0, 0, 500000, tzinfo=timezone.utc)
_WINDOW_END = _ANCHOR + timedelta(seconds=1)
_MICROSECOND = timedelta(microseconds=1)
_HOUR = timedelta(hours=1)

_ENDS_BEFORE = 21
_TOUCH_START = 11
_INSIDE = 13
_TOUCH_END = 12
_STARTS_AFTER = 22
_INCLUDED_PERIODS = frozenset({_TOUCH_START, _INSIDE, _TOUCH_END})


def _open_sla_store(backend: str, tmp_path: Path) -> InMemorySLAStorage | SQLiteSLAStorage:
    """Return a fresh SLA store for ``backend``."""
    if backend == "memory":
        return InMemorySLAStorage()
    if backend == "sqlite":
        return SQLiteSLAStorage(db_path=str(tmp_path / "sla-fence.db"))
    raise ValueError(f"backend must be 'memory' or 'sqlite', got {backend!r}")


def _metric(label: int, period_start: datetime, period_end: datetime) -> SLAMetrics:
    """Build one SLA row; ``latency_p95_ms`` is the assertion label."""
    return SLAMetrics(
        agent_id="urn:asap:agent:sla-fence",
        period_start=period_start,
        period_end=period_end,
        uptime_percent=99.0,
        latency_p95_ms=label,
        error_rate_percent=0.0,
        tasks_completed=1,
        tasks_failed=0,
    )


def _overlap_samples() -> list[SLAMetrics]:
    """Periods that touch the window, sit inside it, or miss it by one microsecond."""
    return [
        _metric(_ENDS_BEFORE, _ANCHOR - _HOUR, _ANCHOR - _MICROSECOND),
        _metric(_TOUCH_START, _ANCHOR - _HOUR, _ANCHOR),
        _metric(_INSIDE, _ANCHOR + _MICROSECOND, _WINDOW_END - _MICROSECOND),
        _metric(_TOUCH_END, _WINDOW_END, _WINDOW_END + _HOUR),
        _metric(_STARTS_AFTER, _WINDOW_END + _MICROSECOND, _WINDOW_END + _HOUR),
    ]


def _breach(breach_id: str, detected_at: datetime) -> SLABreach:
    """Build one breach row identified by ``breach_id``."""
    return SLABreach(
        id=breach_id,
        agent_id="urn:asap:agent:sla-fence",
        breach_type="latency",
        threshold="500ms",
        actual="600ms",
        severity="warning",
        detected_at=detected_at,
    )


def _observed(error_rate_percent: float) -> SLAMetrics:
    """Observed metrics whose only varying field is the error rate."""
    period_start = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
    return SLAMetrics(
        agent_id="urn:asap:agent:sla-fence",
        period_start=period_start,
        period_end=period_start + _HOUR,
        uptime_percent=100.0,
        latency_p95_ms=10,
        error_rate_percent=error_rate_percent,
        tasks_completed=95,
        tasks_failed=5,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_metrics_overlap_keeps_periods_that_only_touch_the_window(
    backend: str,
    tmp_path: Path,
) -> None:
    """Overlap includes a period that ends on ``start`` or begins on ``end``.

    In-memory drops a row only when ``period_end < start`` or
    ``period_start > end``. SQLite keeps ``period_end >= start`` and
    ``period_start <= end``. A containment check would drop both touching
    periods because each extends outside the window.
    """
    store = _open_sla_store(backend, tmp_path)
    for sample in _overlap_samples():
        await store.record_metrics(sample)

    found = await store.query_metrics(start=_ANCHOR, end=_WINDOW_END)
    labels = {row.latency_p95_ms for row in found}

    assert labels == _INCLUDED_PERIODS
    assert await store.count_metrics(start=_ANCHOR, end=_WINDOW_END) == len(_INCLUDED_PERIODS)


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_breach_query_includes_both_endpoints(
    backend: str,
    tmp_path: Path,
) -> None:
    """Breach detection time is inclusive on both ends of the query window."""
    store = _open_sla_store(backend, tmp_path)
    samples = (
        ("before", _ANCHOR - _MICROSECOND),
        ("start", _ANCHOR),
        ("end", _WINDOW_END),
        ("after", _WINDOW_END + _MICROSECOND),
    )
    for breach_id, detected_at in samples:
        await store.record_breach(_breach(breach_id, detected_at))

    found = await store.query_breaches(start=_ANCHOR, end=_WINDOW_END)

    assert {row.id for row in found} == {"start", "end"}


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_query_metrics_rejects_negative_offset(backend: str, tmp_path: Path) -> None:
    """A negative offset is rejected before either store reads rows."""
    store = _open_sla_store(backend, tmp_path)

    with pytest.raises(ValueError, match="offset must be non-negative"):
        await store.query_metrics(offset=-1)


def test_error_rate_five_percent_floor_is_critical_before_double_budget() -> None:
    """A 3% budget is a warning at 4.9% and critical at 5%.

    Twice that budget is 6%, so this boundary is the global 5% floor, not
    the 2x multiplier.
    """
    sla = SLADefinition(max_error_rate="3%")
    below = evaluate_breach_conditions(sla, _observed(4.9))
    at_floor = evaluate_breach_conditions(sla, _observed(5.0))

    assert len(below) == 1
    assert below[0].breach_type == "error_rate"
    assert below[0].severity == "warning"
    assert len(at_floor) == 1
    assert at_floor[0].breach_type == "error_rate"
    assert at_floor[0].severity == "critical"
