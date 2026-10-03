"""Tests for :class:`MeteringStorageBridge` (v2.5.1 S1).

The bridge moved from ``economics.storage.metering_storage_adapter`` to the
state foundation layer (``asap.state.metering.MeteringStorageBridge``) so the
cross-layer adapter lives in the lower layer and economics no longer owns it.
These tests cover the bridge directly (the old adapter tests in
``tests/economics/test_storage.py`` cover the compat shim's delegation).

Contract under test:
- ``record`` flattens a ``UsageEvent`` into a ``UsageMetrics`` row and stores it;
- ``query`` returns ``UsageEvent`` rows for the agent/time window;
- ``aggregate`` returns a state ``UsageAggregate`` for the matching agent and an
  empty one when no data matches;
- period ``h`` excludes events older than one hour.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from asap.economics.metering import UsageMetrics
from asap.economics.storage import InMemoryMeteringStorage
from asap.state.metering import MeteringStorageBridge, UsageAggregate, UsageEvent
from asap.state.metering import UsageMetrics as StateUsageMetrics


def _event(
    task_id: str = "t1",
    agent_id: str = "a1",
    consumer_id: str = "c1",
    tokens_in: int = 10,
    tokens_out: int = 20,
    duration_ms: int = 100,
    api_calls: int = 1,
    timestamp: datetime | None = None,
) -> UsageEvent:
    return UsageEvent(
        task_id=task_id,
        agent_id=agent_id,
        consumer_id=consumer_id,
        metrics=StateUsageMetrics(
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            duration_ms=duration_ms,
            api_calls=api_calls,
        ),
        timestamp=timestamp or datetime(2026, 2, 17, 12, 0, 0, tzinfo=timezone.utc),
    )


class TestMeteringStorageBridgeRecordQuery:
    """record() + query() round-trip through the flat UsageMetrics row."""

    async def test_record_then_query_returns_event(self) -> None:
        bridge = MeteringStorageBridge(InMemoryMeteringStorage())
        await bridge.record(_event())

        results = await bridge.query(
            agent_id="a1",
            start=datetime(2026, 2, 17, 0, 0, 0, tzinfo=timezone.utc),
            end=datetime(2026, 2, 18, 0, 0, 0, tzinfo=timezone.utc),
        )
        assert len(results) == 1
        assert results[0].task_id == "t1"
        assert results[0].metrics.tokens_in == 10
        assert results[0].metrics.tokens_out == 20

    async def test_query_filters_by_agent_id(self) -> None:
        bridge = MeteringStorageBridge(InMemoryMeteringStorage())
        await bridge.record(_event(task_id="t1", agent_id="a1"))
        await bridge.record(_event(task_id="t2", agent_id="a2"))

        results = await bridge.query(
            agent_id="a2",
            start=datetime(2026, 2, 17, 0, 0, 0, tzinfo=timezone.utc),
            end=datetime(2026, 2, 18, 0, 0, 0, tzinfo=timezone.utc),
        )
        assert len(results) == 1
        assert results[0].task_id == "t2"

    async def test_query_outside_window_returns_empty(self) -> None:
        bridge = MeteringStorageBridge(InMemoryMeteringStorage())
        await bridge.record(_event())

        results = await bridge.query(
            agent_id="a1",
            start=datetime(2026, 3, 1, 0, 0, 0, tzinfo=timezone.utc),
            end=datetime(2026, 3, 2, 0, 0, 0, tzinfo=timezone.utc),
        )
        assert results == []


class TestMeteringStorageBridgeAggregate:
    """aggregate() returns a state UsageAggregate for the matching agent."""

    async def test_aggregate_matching_agent(self) -> None:
        backend = InMemoryMeteringStorage()
        bridge = MeteringStorageBridge(backend)
        now = datetime.now(timezone.utc)
        await backend.record(
            UsageMetrics(
                task_id="t1",
                agent_id="a1",
                consumer_id="c1",
                tokens_in=10,
                tokens_out=20,
                duration_ms=100,
                api_calls=3,
                timestamp=now,
            )
        )

        agg = await bridge.aggregate(agent_id="a1", period="hour")
        assert isinstance(agg, UsageAggregate)
        assert agg.agent_id == "a1"
        assert agg.total_tokens == 30
        assert agg.total_api_calls == 3
        assert agg.total_tasks == 1

    async def test_aggregate_no_matching_agent_returns_empty(self) -> None:
        bridge = MeteringStorageBridge(InMemoryMeteringStorage())
        agg = await bridge.aggregate(agent_id="nonexistent", period="day")
        assert agg.agent_id == "nonexistent"
        assert agg.total_tokens == 0
        assert agg.total_tasks == 0

    async def test_aggregate_period_h_excludes_old_events(self) -> None:
        backend = InMemoryMeteringStorage()
        bridge = MeteringStorageBridge(backend)
        now = datetime.now(timezone.utc)
        await backend.record(
            UsageMetrics(
                task_id="old",
                agent_id="a1",
                consumer_id="c1",
                tokens_in=100,
                tokens_out=100,
                api_calls=0,
                timestamp=now - timedelta(hours=2),
            )
        )
        await backend.record(
            UsageMetrics(
                task_id="recent",
                agent_id="a1",
                consumer_id="c1",
                tokens_in=5,
                tokens_out=10,
                api_calls=1,
                timestamp=now - timedelta(minutes=30),
            )
        )

        agg = await bridge.aggregate(agent_id="a1", period="h")
        assert agg.agent_id == "a1"
        assert agg.total_tokens == 15
        assert agg.total_api_calls == 1

    async def test_aggregate_today_excludes_previous_utc_day(self) -> None:
        """``today`` is the current UTC calendar day, not a rolling 24 hours."""
        backend = InMemoryMeteringStorage()
        bridge = MeteringStorageBridge(backend)
        now = datetime.now(timezone.utc)
        start_of_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        await backend.record(
            UsageMetrics(
                task_id="yesterday",
                agent_id="a1",
                consumer_id="c1",
                tokens_in=100,
                tokens_out=100,
                api_calls=4,
                timestamp=start_of_today - timedelta(seconds=1),
            )
        )
        await backend.record(
            UsageMetrics(
                task_id="today",
                agent_id="a1",
                consumer_id="c1",
                tokens_in=5,
                tokens_out=25,
                api_calls=1,
                timestamp=now,
            )
        )

        agg = await bridge.aggregate(agent_id="a1", period="today")
        assert agg.period == "today"
        assert agg.total_tokens == 30
        assert agg.total_tasks == 1
        assert agg.total_api_calls == 1

    async def test_aggregate_unknown_period_no_time_filter(self) -> None:
        backend = InMemoryMeteringStorage()
        bridge = MeteringStorageBridge(backend)
        await backend.record(
            UsageMetrics(
                task_id="t1",
                agent_id="a1",
                consumer_id="c1",
                tokens_in=10,
                tokens_out=20,
                api_calls=0,
                timestamp=datetime(2020, 1, 1, tzinfo=timezone.utc),
            )
        )

        agg = await bridge.aggregate(agent_id="a1", period="all")
        assert agg.agent_id == "a1"
        assert agg.total_tokens == 30


class TestMeteringStorageBridgeSatisfiesMeteringStore:
    """The bridge structurally satisfies the state ``MeteringStore`` protocol."""

    def test_has_record_query_aggregate(self) -> None:
        bridge = MeteringStorageBridge(InMemoryMeteringStorage())
        assert callable(bridge.record)
        assert callable(bridge.query)
        assert callable(bridge.aggregate)
