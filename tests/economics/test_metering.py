"""Tests for ASAP economics metering models (UsageMetrics, aggregation)."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from asap.economics.metering import (
    BatchUsageRequest,
    UsageAggregateByAgent,
    UsageAggregateByConsumer,
    UsageAggregateByPeriod,
    UsageMetrics,
)
from asap.state.metering import UsageEvent, UsageMetrics as StateUsageMetrics


@pytest.fixture
def sample_usage_metrics() -> UsageMetrics:
    """Create a sample UsageMetrics for testing."""
    return UsageMetrics(
        task_id="task_123",
        agent_id="urn:asap:agent:provider",
        consumer_id="urn:asap:agent:consumer",
        tokens_in=1500,
        tokens_out=2300,
        duration_ms=4500,
        api_calls=3,
        timestamp=datetime(2026, 2, 16, 12, 0, 0, tzinfo=timezone.utc),
    )


@pytest.fixture
def sample_usage_event() -> UsageEvent:
    """Create a sample UsageEvent for conversion tests."""
    return UsageEvent(
        task_id="task_456",
        agent_id="agent_01",
        consumer_id="consumer_01",
        metrics=StateUsageMetrics(
            tokens_in=10,
            tokens_out=20,
            duration_ms=100,
            api_calls=1,
        ),
        timestamp=datetime(2026, 2, 16, 14, 30, 0, tzinfo=timezone.utc),
    )


class TestUsageMetricsValidation:
    """Test UsageMetrics model validation."""

    def test_valid_usage_metrics(
        self,
        sample_usage_metrics: UsageMetrics,
    ) -> None:
        """UsageMetrics accepts valid fields."""
        assert sample_usage_metrics.task_id == "task_123"
        assert sample_usage_metrics.agent_id == "urn:asap:agent:provider"
        assert sample_usage_metrics.consumer_id == "urn:asap:agent:consumer"
        assert sample_usage_metrics.tokens_in == 1500
        assert sample_usage_metrics.tokens_out == 2300
        assert sample_usage_metrics.duration_ms == 4500
        assert sample_usage_metrics.api_calls == 3

    def test_defaults_for_optional_metrics(self) -> None:
        """UsageMetrics defaults tokens_in, tokens_out, duration_ms, api_calls to 0."""
        record = UsageMetrics(
            task_id="t1",
            agent_id="a1",
            consumer_id="c1",
            timestamp=datetime.now(timezone.utc),
        )
        assert record.tokens_in == 0
        assert record.tokens_out == 0
        assert record.duration_ms == 0
        assert record.api_calls == 0

    def test_rejects_negative_tokens(self) -> None:
        """UsageMetrics rejects negative tokens_in."""
        with pytest.raises(ValidationError):
            UsageMetrics(
                task_id="t1",
                agent_id="a1",
                consumer_id="c1",
                tokens_in=-1,
                timestamp=datetime.now(timezone.utc),
            )

    def test_rejects_negative_duration(self) -> None:
        """UsageMetrics rejects negative duration_ms."""
        with pytest.raises(ValidationError):
            UsageMetrics(
                task_id="t1",
                agent_id="a1",
                consumer_id="c1",
                duration_ms=-100,
                timestamp=datetime.now(timezone.utc),
            )

    def test_rejects_extra_fields(self) -> None:
        """UsageMetrics forbids extra fields (ASAPBaseModel)."""
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            UsageMetrics(
                task_id="t1",
                agent_id="a1",
                consumer_id="c1",
                timestamp=datetime.now(timezone.utc),
                unknown_field="x",
            )


class TestUsageMetricsConversion:
    """Test UsageMetrics <-> UsageEvent conversion."""

    def test_to_usage_event(
        self,
        sample_usage_metrics: UsageMetrics,
    ) -> None:
        """to_usage_event produces valid UsageEvent for MeteringStore."""
        event = sample_usage_metrics.to_usage_event()
        assert isinstance(event, UsageEvent)
        assert event.task_id == sample_usage_metrics.task_id
        assert event.agent_id == sample_usage_metrics.agent_id
        assert event.consumer_id == sample_usage_metrics.consumer_id
        assert event.metrics.tokens_in == sample_usage_metrics.tokens_in
        assert event.metrics.tokens_out == sample_usage_metrics.tokens_out
        assert event.metrics.duration_ms == sample_usage_metrics.duration_ms
        assert event.metrics.api_calls == sample_usage_metrics.api_calls
        assert event.timestamp == sample_usage_metrics.timestamp

    def test_from_usage_event(
        self,
        sample_usage_event: UsageEvent,
    ) -> None:
        """from_usage_event reconstructs UsageMetrics from UsageEvent."""
        record = UsageMetrics.from_usage_event(sample_usage_event)
        assert record.task_id == sample_usage_event.task_id
        assert record.agent_id == sample_usage_event.agent_id
        assert record.consumer_id == sample_usage_event.consumer_id
        assert record.tokens_in == sample_usage_event.metrics.tokens_in
        assert record.tokens_out == sample_usage_event.metrics.tokens_out
        assert record.duration_ms == sample_usage_event.metrics.duration_ms
        assert record.api_calls == sample_usage_event.metrics.api_calls
        assert record.timestamp == sample_usage_event.timestamp

    def test_roundtrip_conversion(
        self,
        sample_usage_metrics: UsageMetrics,
    ) -> None:
        """UsageMetrics -> UsageEvent -> UsageMetrics preserves data."""
        event = sample_usage_metrics.to_usage_event()
        restored = UsageMetrics.from_usage_event(event)
        assert restored.task_id == sample_usage_metrics.task_id
        assert restored.agent_id == sample_usage_metrics.agent_id
        assert restored.consumer_id == sample_usage_metrics.consumer_id
        assert restored.tokens_in == sample_usage_metrics.tokens_in
        assert restored.tokens_out == sample_usage_metrics.tokens_out
        assert restored.duration_ms == sample_usage_metrics.duration_ms
        assert restored.api_calls == sample_usage_metrics.api_calls
        assert restored.timestamp == sample_usage_metrics.timestamp


class TestUsageAggregateByAgent:
    """Test UsageAggregateByAgent model."""

    def test_valid_aggregate(self) -> None:
        """UsageAggregateByAgent accepts valid fields."""
        agg = UsageAggregateByAgent(
            agent_id="agent_01",
            period="day",
            total_tokens=5000,
            total_duration_ms=12000,
            total_tasks=10,
            total_api_calls=25,
            avg_tokens_per_task=500.0,
            avg_duration_ms_per_task=1200.0,
        )
        assert agg.agent_id == "agent_01"
        assert agg.period == "day"
        assert agg.total_tokens == 5000
        assert agg.avg_tokens_per_task == 500.0

    def test_defaults(self) -> None:
        """UsageAggregateByAgent defaults numeric fields to 0."""
        agg = UsageAggregateByAgent(agent_id="a1", period="hour")
        assert agg.total_tokens == 0
        assert agg.total_duration_ms == 0
        assert agg.total_tasks == 0
        assert agg.total_api_calls == 0
        assert agg.avg_tokens_per_task == 0.0
        assert agg.avg_duration_ms_per_task == 0.0


class TestUsageAggregateByConsumer:
    """Test UsageAggregateByConsumer model."""

    def test_valid_aggregate(self) -> None:
        """UsageAggregateByConsumer accepts valid fields."""
        agg = UsageAggregateByConsumer(
            consumer_id="consumer_01",
            period="week",
            total_tokens=10000,
            total_duration_ms=30000,
            total_tasks=20,
            total_api_calls=50,
            avg_tokens_per_task=500.0,
        )
        assert agg.consumer_id == "consumer_01"
        assert agg.period == "week"
        assert agg.total_tokens == 10000


class TestUsageAggregateByPeriod:
    """Test UsageAggregateByPeriod model."""

    def test_valid_aggregate(self) -> None:
        """UsageAggregateByPeriod accepts valid fields."""
        agg = UsageAggregateByPeriod(
            period="2026-02-16",
            total_tokens=15000,
            total_duration_ms=45000,
            total_tasks=30,
            total_api_calls=75,
            unique_agents=5,
            unique_consumers=8,
        )
        assert agg.period == "2026-02-16"
        assert agg.unique_agents == 5
        assert agg.unique_consumers == 8


def _batch_event(index: int) -> UsageMetrics:
    """One usage row for batch-size validation."""
    return UsageMetrics(
        task_id=f"task_{index}",
        agent_id="urn:asap:agent:batch",
        consumer_id="urn:asap:agent:consumer",
        timestamp=datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc),
    )


class TestBatchUsageRequestLimits:
    """Ingress batches are capped so one request cannot record an unbounded ledger."""

    def test_accepts_one_thousand_events(self) -> None:
        """The documented maximum of 1000 events is valid."""
        batch = BatchUsageRequest(events=[_batch_event(index) for index in range(1000)])
        assert len(batch.events) == 1000

    def test_rejects_empty_and_one_past_the_cap(self) -> None:
        """Zero events and 1001 events are rejected."""
        with pytest.raises(ValidationError):
            BatchUsageRequest(events=[])
        with pytest.raises(ValidationError, match="1000"):
            BatchUsageRequest(events=[_batch_event(index) for index in range(1001)])
