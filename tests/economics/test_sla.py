"""Tests for SLA models and metrics collection helpers."""

from datetime import datetime, timezone

import pytest

from asap.economics.sla import (
    SLAMetrics,
    SLABreach,
    BreachDetector,
    aggregate_sla_metrics,
    compute_error_rate_percent,
    compute_latency_p95_ms,
    compute_uptime_percent,
    evaluate_breach_conditions,
    parse_percentage,
    rolling_window_bounds,
)
from asap.economics.sla_storage import InMemorySLAStorage
from asap.models.entities import SLADefinition


class TestSLAMetrics:
    """Tests for SLAMetrics model."""

    def test_sla_metrics_creation(self) -> None:
        """SLAMetrics accepts all required fields."""
        start = datetime(2026, 2, 18, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 2, 18, 1, 0, 0, tzinfo=timezone.utc)
        m = SLAMetrics(
            agent_id="urn:asap:agent:test",
            period_start=start,
            period_end=end,
            uptime_percent=99.5,
            latency_p95_ms=120,
            error_rate_percent=0.5,
            tasks_completed=100,
            tasks_failed=1,
        )
        assert m.agent_id == "urn:asap:agent:test"
        assert m.uptime_percent == 99.5
        assert m.latency_p95_ms == 120
        assert m.tasks_completed == 100
        assert m.tasks_failed == 1

    def test_sla_metrics_serialization_roundtrip(self) -> None:
        """SLAMetrics round-trips via model_dump and model_validate."""
        start = datetime(2026, 2, 18, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 2, 18, 1, 0, 0, tzinfo=timezone.utc)
        m = SLAMetrics(
            agent_id="urn:asap:agent:a",
            period_start=start,
            period_end=end,
            uptime_percent=100.0,
            latency_p95_ms=50,
            error_rate_percent=0.0,
            tasks_completed=10,
            tasks_failed=0,
        )
        data = m.model_dump()
        restored = SLAMetrics.model_validate(data)
        assert restored.agent_id == m.agent_id
        assert restored.uptime_percent == m.uptime_percent


class TestSLABreach:
    """Tests for SLABreach model."""

    def test_sla_breach_creation(self) -> None:
        """SLABreach accepts all fields; resolved_at optional."""
        detected = datetime(2026, 2, 18, 12, 0, 0, tzinfo=timezone.utc)
        b = SLABreach(
            id="breach_01",
            agent_id="urn:asap:agent:test",
            breach_type="latency",
            threshold="500ms",
            actual="1200ms",
            severity="critical",
            detected_at=detected,
        )
        assert b.id == "breach_01"
        assert b.breach_type == "latency"
        assert b.resolved_at is None

    def test_sla_breach_with_resolved_at(self) -> None:
        """SLABreach accepts resolved_at."""
        detected = datetime(2026, 2, 18, 12, 0, 0, tzinfo=timezone.utc)
        resolved = datetime(2026, 2, 18, 12, 30, 0, tzinfo=timezone.utc)
        b = SLABreach(
            id="b2",
            agent_id="urn:asap:agent:a",
            breach_type="availability",
            threshold="99.5%",
            actual="98%",
            severity="warning",
            detected_at=detected,
            resolved_at=resolved,
        )
        assert b.resolved_at == resolved


class TestComputeUptimePercent:
    """Tests for compute_uptime_percent."""

    def test_full_uptime(self) -> None:
        """100% when all checks ok."""
        assert compute_uptime_percent(10, 10) == 100.0

    def test_half_uptime(self) -> None:
        """50% when half ok."""
        assert compute_uptime_percent(5, 10) == 50.0

    def test_zero_total_returns_100(self) -> None:
        """No checks => 100% (avoid div by zero)."""
        assert compute_uptime_percent(0, 0) == 100.0

    def test_clamped_to_100(self) -> None:
        """More ok than total is clamped to 100."""
        assert compute_uptime_percent(12, 10) == 100.0


class TestComputeLatencyP95:
    """Tests for compute_latency_p95_ms."""

    def test_empty_returns_zero(self) -> None:
        """Empty list returns 0."""
        assert compute_latency_p95_ms([]) == 0

    def test_single_value(self) -> None:
        """Single duration returns that value."""
        assert compute_latency_p95_ms([100]) == 100

    def test_p95_approximate(self) -> None:
        """P95 is approximately the 95th percentile index."""
        # 100 values 0..99; p95 index ~ 94.05 -> value 94
        values = list(range(100))
        assert compute_latency_p95_ms(values) == 94


class TestComputeErrorRatePercent:
    """Tests for compute_error_rate_percent."""

    def test_no_failures(self) -> None:
        """0% when no failures."""
        assert compute_error_rate_percent(10, 0) == 0.0

    def test_half_failed(self) -> None:
        """50% when half failed."""
        assert compute_error_rate_percent(5, 5) == 50.0

    def test_all_failed(self) -> None:
        """100% when all failed."""
        assert compute_error_rate_percent(0, 10) == 100.0

    def test_no_tasks_returns_zero(self) -> None:
        """No tasks => 0%."""
        assert compute_error_rate_percent(0, 0) == 0.0


class TestRollingWindowBounds:
    """Tests for rolling_window_bounds."""

    def test_1h_window(self) -> None:
        """1h window returns end - 1 hour, end."""
        end = datetime(2026, 2, 18, 14, 0, 0, tzinfo=timezone.utc)
        start, e = rolling_window_bounds("1h", end)
        assert e == end
        assert (end - start).total_seconds() == 3600

    def test_24h_window(self) -> None:
        """24h window spans one day."""
        end = datetime(2026, 2, 18, 12, 0, 0, tzinfo=timezone.utc)
        start, e = rolling_window_bounds("24h", end)
        assert (end - start).total_seconds() == 24 * 3600

    def test_7d_window(self) -> None:
        """7d window spans 7 days."""
        end = datetime(2026, 2, 18, 0, 0, 0, tzinfo=timezone.utc)
        start, e = rolling_window_bounds("7d", end)
        assert (end - start).days == 7

    def test_30d_window(self) -> None:
        """30d window spans 30 days."""
        end = datetime(2026, 2, 18, 0, 0, 0, tzinfo=timezone.utc)
        start, e = rolling_window_bounds("30d", end)
        assert (end - start).days == 30

    def test_invalid_window_raises(self) -> None:
        """Invalid window name raises ValueError."""
        with pytest.raises(ValueError, match="window must be one of"):
            rolling_window_bounds("2d")

    def test_default_end_is_now(self) -> None:
        """When end is None, end is close to now (within 2s)."""
        start, end = rolling_window_bounds("1h")
        now = datetime.now(timezone.utc)
        assert (now - end).total_seconds() < 2
        assert (end - start).total_seconds() == 3600


class TestAggregateSlaMetrics:
    """Tests for aggregate_sla_metrics."""

    def test_empty_returns_none(self) -> None:
        """Empty list returns None."""
        assert aggregate_sla_metrics([]) is None

    def test_single_returns_same(self) -> None:
        """Single metrics returned as-is (same agent)."""
        start = datetime(2026, 2, 18, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 2, 18, 1, 0, 0, tzinfo=timezone.utc)
        m = SLAMetrics(
            agent_id="urn:asap:agent:a",
            period_start=start,
            period_end=end,
            uptime_percent=99.0,
            latency_p95_ms=100,
            error_rate_percent=1.0,
            tasks_completed=90,
            tasks_failed=10,
        )
        agg = aggregate_sla_metrics([m])
        assert agg is not None
        assert agg.agent_id == m.agent_id
        assert agg.uptime_percent == 99.0
        assert agg.tasks_completed == 90
        assert agg.tasks_failed == 10

    def test_aggregate_multiple(self) -> None:
        """Multiple metrics aggregated: sums for tasks, weighted averages for rates."""
        start1 = datetime(2026, 2, 18, 0, 0, 0, tzinfo=timezone.utc)
        end1 = datetime(2026, 2, 18, 1, 0, 0, tzinfo=timezone.utc)
        start2 = datetime(2026, 2, 18, 1, 0, 0, tzinfo=timezone.utc)
        end2 = datetime(2026, 2, 18, 2, 0, 0, tzinfo=timezone.utc)
        m1 = SLAMetrics(
            agent_id="urn:asap:agent:a",
            period_start=start1,
            period_end=end1,
            uptime_percent=100.0,
            latency_p95_ms=50,
            error_rate_percent=0.0,
            tasks_completed=10,
            tasks_failed=0,
        )
        m2 = SLAMetrics(
            agent_id="urn:asap:agent:a",
            period_start=start2,
            period_end=end2,
            uptime_percent=90.0,
            latency_p95_ms=200,
            error_rate_percent=10.0,
            tasks_completed=9,
            tasks_failed=1,
        )
        agg = aggregate_sla_metrics([m1, m2])
        assert agg is not None
        assert agg.agent_id == "urn:asap:agent:a"
        assert agg.period_start == start1
        assert agg.period_end == end2
        assert agg.tasks_completed == 19
        assert agg.tasks_failed == 1
        assert 90.0 <= agg.uptime_percent <= 100.0
        assert agg.latency_p95_ms in (50, 200)  # p95 of [50, 200]

    def test_zero_task_windows_use_unweighted_mean(self) -> None:
        """Idle windows with no tasks average uptime and error rate directly."""
        start1 = datetime(2026, 2, 18, 0, 0, 0, tzinfo=timezone.utc)
        end1 = datetime(2026, 2, 18, 1, 0, 0, tzinfo=timezone.utc)
        start2 = datetime(2026, 2, 18, 1, 0, 0, tzinfo=timezone.utc)
        end2 = datetime(2026, 2, 18, 2, 0, 0, tzinfo=timezone.utc)
        idle_high = SLAMetrics(
            agent_id="urn:asap:agent:a",
            period_start=start1,
            period_end=end1,
            uptime_percent=100.0,
            latency_p95_ms=10,
            error_rate_percent=0.0,
            tasks_completed=0,
            tasks_failed=0,
        )
        idle_low = SLAMetrics(
            agent_id="urn:asap:agent:a",
            period_start=start2,
            period_end=end2,
            uptime_percent=50.0,
            latency_p95_ms=30,
            error_rate_percent=20.0,
            tasks_completed=0,
            tasks_failed=0,
        )
        agg = aggregate_sla_metrics([idle_high, idle_low])
        assert agg is not None
        assert agg.period_start == start1
        assert agg.period_end == end2
        assert agg.tasks_completed == 0
        assert agg.tasks_failed == 0
        assert agg.uptime_percent == 75.0
        assert agg.error_rate_percent == 10.0
        assert agg.latency_p95_ms == 10

    def test_zero_task_window_does_not_dilute_busy_window(self) -> None:
        """A zero-task window contributes nothing to task-weighted rates."""
        start1 = datetime(2026, 2, 18, 0, 0, 0, tzinfo=timezone.utc)
        end1 = datetime(2026, 2, 18, 1, 0, 0, tzinfo=timezone.utc)
        start2 = datetime(2026, 2, 18, 1, 0, 0, tzinfo=timezone.utc)
        end2 = datetime(2026, 2, 18, 2, 0, 0, tzinfo=timezone.utc)
        idle = SLAMetrics(
            agent_id="urn:asap:agent:a",
            period_start=start1,
            period_end=end1,
            uptime_percent=0.0,
            latency_p95_ms=1000,
            error_rate_percent=100.0,
            tasks_completed=0,
            tasks_failed=0,
        )
        busy = SLAMetrics(
            agent_id="urn:asap:agent:a",
            period_start=start2,
            period_end=end2,
            uptime_percent=100.0,
            latency_p95_ms=40,
            error_rate_percent=0.0,
            tasks_completed=8,
            tasks_failed=2,
        )
        agg = aggregate_sla_metrics([idle, busy])
        assert agg is not None
        assert agg.tasks_completed == 8
        assert agg.tasks_failed == 2
        assert agg.uptime_percent == 100.0
        assert agg.error_rate_percent == 0.0
        assert agg.latency_p95_ms == 40


class TestParsePercentage:
    """Tests for parse_percentage (breach condition helpers)."""

    def test_with_percent_sign(self) -> None:
        """Parses '99.5%' to 99.5."""
        assert parse_percentage("99.5%") == 99.5

    def test_without_percent_sign(self) -> None:
        """Parses '1' to 1.0."""
        assert parse_percentage("1") == 1.0

    def test_whitespace_tolerated(self) -> None:
        """Whitespace around value is stripped."""
        assert parse_percentage("  2.5 %  ") == 2.5

    def test_clamped_to_100(self) -> None:
        """Values above 100 are clamped to 100."""
        assert parse_percentage("150%") == 100.0

    def test_clamped_to_zero(self) -> None:
        """Negative values are clamped to 0."""
        assert parse_percentage("-10%") == 0.0

    def test_invalid_raises(self) -> None:
        """Invalid string raises ValueError."""
        with pytest.raises(ValueError, match="Invalid percentage"):
            parse_percentage("not a number")


class TestEvaluateBreachConditions:
    """Tests for evaluate_breach_conditions."""

    def _metrics(
        self,
        uptime: float = 99.5,
        latency_ms: int = 100,
        error_rate: float = 0.5,
    ) -> SLAMetrics:
        start = datetime(2026, 2, 18, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 2, 18, 1, 0, 0, tzinfo=timezone.utc)
        total = int(100 * (1 + (error_rate / 100)))
        completed = max(0, total - int(total * error_rate / 100))
        failed = total - completed
        return SLAMetrics(
            agent_id="urn:asap:agent:test",
            period_start=start,
            period_end=end,
            uptime_percent=uptime,
            latency_p95_ms=latency_ms,
            error_rate_percent=error_rate,
            tasks_completed=completed,
            tasks_failed=failed,
        )

    def test_no_sla_no_breaches(self) -> None:
        """When SLA is None, no breaches are returned."""
        metrics = self._metrics(uptime=50.0, latency_ms=2000, error_rate=10.0)
        assert evaluate_breach_conditions(None, metrics) == []

    def test_empty_sla_no_breaches(self) -> None:
        """When SLA has no thresholds set, no breaches."""
        sla = SLADefinition()
        metrics = self._metrics(uptime=50.0, latency_ms=2000, error_rate=10.0)
        assert evaluate_breach_conditions(sla, metrics) == []

    def test_availability_breach_uptime_below_threshold(self) -> None:
        """Uptime below promised availability triggers availability breach."""
        sla = SLADefinition(availability="99.5%")
        metrics = self._metrics(uptime=98.0, latency_ms=50, error_rate=0.0)
        results = evaluate_breach_conditions(sla, metrics)
        assert len(results) == 1
        assert results[0].breach_type == "availability"
        assert results[0].threshold == "99.5%"
        assert results[0].actual == "98.0%"
        assert results[0].severity == "warning"

    def test_availability_breach_critical_when_below_95(self) -> None:
        """Uptime below 95% yields critical severity."""
        sla = SLADefinition(availability="99%")
        metrics = self._metrics(uptime=90.0, latency_ms=50, error_rate=0.0)
        results = evaluate_breach_conditions(sla, metrics)
        assert len(results) == 1
        assert results[0].breach_type == "availability"
        assert results[0].severity == "critical"

    def test_no_availability_breach_when_at_or_above(self) -> None:
        """No breach when uptime meets or exceeds threshold."""
        sla = SLADefinition(availability="99%")
        metrics = self._metrics(uptime=99.0, latency_ms=50, error_rate=0.0)
        assert evaluate_breach_conditions(sla, metrics) == []

    def test_latency_breach_above_max(self) -> None:
        """Latency above max_latency_p95_ms triggers latency breach."""
        sla = SLADefinition(max_latency_p95_ms=500)
        metrics = self._metrics(uptime=100.0, latency_ms=600, error_rate=0.0)
        results = evaluate_breach_conditions(sla, metrics)
        assert len(results) == 1
        assert results[0].breach_type == "latency"
        assert results[0].threshold == "500ms"
        assert results[0].actual == "600ms"
        assert results[0].severity == "warning"

    def test_latency_breach_critical_when_double_threshold(self) -> None:
        """Latency >= 2x threshold yields critical."""
        sla = SLADefinition(max_latency_p95_ms=500)
        metrics = self._metrics(uptime=100.0, latency_ms=1000, error_rate=0.0)
        results = evaluate_breach_conditions(sla, metrics)
        assert len(results) == 1
        assert results[0].breach_type == "latency"
        assert results[0].severity == "critical"

    def test_error_rate_breach_above_max(self) -> None:
        """Error rate above max_error_rate triggers error_rate breach."""
        sla = SLADefinition(max_error_rate="1%")
        metrics = self._metrics(uptime=100.0, latency_ms=50, error_rate=2.0)
        results = evaluate_breach_conditions(sla, metrics)
        assert len(results) == 1
        assert results[0].breach_type == "error_rate"
        assert results[0].threshold == "1.0%"
        assert results[0].actual == "2.0%"

    def test_error_rate_breach_critical_when_above_5_percent(self) -> None:
        """Error rate >= 5% yields critical."""
        sla = SLADefinition(max_error_rate="3%")
        metrics = self._metrics(uptime=100.0, latency_ms=50, error_rate=6.0)
        results = evaluate_breach_conditions(sla, metrics)
        assert len(results) == 1
        assert results[0].breach_type == "error_rate"
        assert results[0].severity == "critical"

    def test_multiple_breaches(self) -> None:
        """All three breach types can be returned when all thresholds are exceeded."""
        sla = SLADefinition(
            availability="99%",
            max_latency_p95_ms=100,
            max_error_rate="0.5%",
        )
        metrics = self._metrics(uptime=97.0, latency_ms=200, error_rate=2.0)
        results = evaluate_breach_conditions(sla, metrics)
        assert len(results) == 3
        types = {r.breach_type for r in results}
        assert types == {"availability", "latency", "error_rate"}


class TestBreachDetector:
    """Tests for BreachDetector."""

    def _metrics(
        self,
        agent_id: str = "urn:asap:agent:test",
        uptime: float = 99.5,
        latency_ms: int = 100,
        error_rate: float = 0.5,
    ) -> SLAMetrics:
        start = datetime(2026, 2, 18, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 2, 18, 1, 0, 0, tzinfo=timezone.utc)
        total = 100
        completed = max(0, total - int(total * error_rate / 100))
        failed = total - completed
        return SLAMetrics(
            agent_id=agent_id,
            period_start=start,
            period_end=end,
            uptime_percent=uptime,
            latency_p95_ms=latency_ms,
            error_rate_percent=error_rate,
            tasks_completed=completed,
            tasks_failed=failed,
        )

    @pytest.mark.asyncio
    async def test_check_and_record_stores_breaches(self) -> None:
        """BreachDetector records breaches to storage."""
        storage = InMemorySLAStorage()
        detector = BreachDetector(storage)
        sla = SLADefinition(availability="99.9%", max_latency_p95_ms=50)
        metrics = self._metrics(uptime=99.0, latency_ms=100, error_rate=0.0)
        recorded = await detector.check_and_record("urn:asap:agent:a", sla, metrics)
        assert len(recorded) == 2  # availability + latency
        breaches = await storage.query_breaches(agent_id="urn:asap:agent:a")
        assert len(breaches) == 2
        assert all(b.agent_id == "urn:asap:agent:a" for b in breaches)

    @pytest.mark.asyncio
    async def test_check_and_record_no_breach_returns_empty(self) -> None:
        """When no conditions are breached, returns empty list and stores nothing."""
        storage = InMemorySLAStorage()
        detector = BreachDetector(storage)
        sla = SLADefinition(availability="99%", max_latency_p95_ms=500)
        metrics = self._metrics(uptime=99.5, latency_ms=100, error_rate=0.5)
        recorded = await detector.check_and_record("urn:asap:agent:a", sla, metrics)
        assert recorded == []
        breaches = await storage.query_breaches()
        assert len(breaches) == 0

    @pytest.mark.asyncio
    async def test_alert_callback_invoked(self) -> None:
        """Custom on_breach callback is invoked for each recorded breach."""
        storage = InMemorySLAStorage()
        received: list[SLABreach] = []

        async def on_breach(b: SLABreach) -> None:
            received.append(b)

        detector = BreachDetector(storage, on_breach=on_breach)
        sla = SLADefinition(max_error_rate="0.1%")
        metrics = self._metrics(uptime=100.0, latency_ms=10, error_rate=2.0)
        recorded = await detector.check_and_record("urn:asap:agent:b", sla, metrics)
        assert len(recorded) == 1
        assert len(received) == 1
        assert received[0].breach_type == "error_rate"
        assert received[0].agent_id == "urn:asap:agent:b"

    @pytest.mark.asyncio
    async def test_sync_callback_supported(self) -> None:
        """Sync on_breach callback (returning None) is supported."""
        storage = InMemorySLAStorage()
        received: list[SLABreach] = []

        def sync_on_breach(b: SLABreach) -> None:
            received.append(b)

        detector = BreachDetector(storage, on_breach=sync_on_breach)
        sla = SLADefinition(max_latency_p95_ms=10)
        metrics = self._metrics(uptime=100.0, latency_ms=500, error_rate=0.0)
        await detector.check_and_record("urn:asap:agent:c", sla, metrics)
        assert len(received) == 1
        assert received[0].breach_type == "latency"
