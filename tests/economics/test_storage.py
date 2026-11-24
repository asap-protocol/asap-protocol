"""Tests for MeteringStorage interface and implementations."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from asap.economics import (
    InMemoryMeteringStorage,
    MeteringQuery,
    SQLiteMeteringStorage,
    UsageMetrics,
)
from asap.economics.storage import _period_to_metering_query
from asap.state.metering import MeteringStorageBridge


@pytest.fixture
def in_memory_storage() -> InMemoryMeteringStorage:
    """Fresh InMemoryMeteringStorage for each test."""
    return InMemoryMeteringStorage()


@pytest.fixture
def sqlite_storage(tmp_path) -> SQLiteMeteringStorage:
    """Fresh SQLiteMeteringStorage for each test (isolated DB)."""
    return SQLiteMeteringStorage(db_path=str(tmp_path / "test_usage.db"))


@pytest.fixture
def sample_metrics() -> UsageMetrics:
    """Sample UsageMetrics for testing."""
    return UsageMetrics(
        task_id="task_01",
        agent_id="agent_01",
        consumer_id="consumer_01",
        tokens_in=10,
        tokens_out=20,
        duration_ms=100,
        api_calls=1,
        timestamp=datetime(2026, 2, 17, 12, 0, 0, tzinfo=timezone.utc),
    )


class TestMeteringStorageProtocol:
    """Test MeteringStorage protocol contract."""

    def test_in_memory_implements_protocol(self) -> None:
        """InMemoryMeteringStorage conforms to MeteringStorage."""
        from asap.economics.storage import MeteringStorage

        store = InMemoryMeteringStorage()
        assert isinstance(store, MeteringStorage)

    def test_sqlite_implements_protocol(self, sqlite_storage: SQLiteMeteringStorage) -> None:
        """SQLiteMeteringStorage conforms to MeteringStorage."""
        from asap.economics.storage import MeteringStorage

        assert isinstance(sqlite_storage, MeteringStorage)


class TestInMemoryMeteringStorageRecordAndQuery:
    """Test InMemoryMeteringStorage record and query."""

    @pytest.mark.asyncio
    async def test_record_and_query_returns_metrics(
        self,
        in_memory_storage: InMemoryMeteringStorage,
        sample_metrics: UsageMetrics,
    ) -> None:
        """Record stores metrics; query returns them in range."""
        await in_memory_storage.record(sample_metrics)
        start = datetime(2026, 2, 17, 11, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 2, 17, 14, 0, 0, tzinfo=timezone.utc)
        results = await in_memory_storage.query(MeteringQuery(start=start, end=end))
        assert len(results) == 1
        assert results[0].task_id == sample_metrics.task_id
        assert results[0].tokens_in == 10
        assert results[0].tokens_out == 20

    @pytest.mark.asyncio
    async def test_query_filters_by_agent_id(
        self,
        in_memory_storage: InMemoryMeteringStorage,
        sample_metrics: UsageMetrics,
    ) -> None:
        """Query filters by agent_id when provided."""
        await in_memory_storage.record(sample_metrics)
        start = datetime(2026, 2, 17, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 2, 18, 0, 0, 0, tzinfo=timezone.utc)
        assert (
            len(
                await in_memory_storage.query(
                    MeteringQuery(agent_id="agent_01", start=start, end=end)
                )
            )
            == 1
        )
        assert (
            len(
                await in_memory_storage.query(MeteringQuery(agent_id="other", start=start, end=end))
            )
            == 0
        )

    @pytest.mark.asyncio
    async def test_query_filters_by_consumer_id(
        self,
        in_memory_storage: InMemoryMeteringStorage,
        sample_metrics: UsageMetrics,
    ) -> None:
        """Query filters by consumer_id when provided."""
        await in_memory_storage.record(sample_metrics)
        start = datetime(2026, 2, 17, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 2, 18, 0, 0, 0, tzinfo=timezone.utc)
        assert (
            len(
                await in_memory_storage.query(
                    MeteringQuery(consumer_id="consumer_01", start=start, end=end)
                )
            )
            == 1
        )
        assert (
            len(
                await in_memory_storage.query(
                    MeteringQuery(consumer_id="other", start=start, end=end)
                )
            )
            == 0
        )

    @pytest.mark.asyncio
    async def test_query_filters_by_task_id(
        self,
        in_memory_storage: InMemoryMeteringStorage,
        sample_metrics: UsageMetrics,
    ) -> None:
        """Query filters by task_id when provided."""
        await in_memory_storage.record(sample_metrics)
        await in_memory_storage.record(
            UsageMetrics(
                task_id="task_02",
                agent_id="agent_01",
                consumer_id="consumer_01",
                timestamp=datetime(2026, 2, 17, 13, 0, 0, tzinfo=timezone.utc),
            )
        )
        results = await in_memory_storage.query(MeteringQuery(task_id="task_01"))
        assert len(results) == 1
        assert results[0].task_id == "task_01"
        assert len(await in_memory_storage.query(MeteringQuery(task_id="nonexistent"))) == 0

    @pytest.mark.asyncio
    async def test_query_pagination_limit_offset(
        self,
        in_memory_storage: InMemoryMeteringStorage,
    ) -> None:
        """Query respects limit and offset."""
        base = datetime(2026, 2, 17, 12, 0, 0, tzinfo=timezone.utc)
        for i in range(5):
            await in_memory_storage.record(
                UsageMetrics(
                    task_id=f"t{i}",
                    agent_id="a1",
                    consumer_id="c1",
                    timestamp=base + timedelta(minutes=i),
                )
            )
        start = datetime(2026, 2, 17, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 2, 18, 0, 0, 0, tzinfo=timezone.utc)
        all_results = await in_memory_storage.query(MeteringQuery(start=start, end=end))
        assert len(all_results) == 5
        limited = await in_memory_storage.query(MeteringQuery(start=start, end=end, limit=2))
        assert len(limited) == 2
        offset_results = await in_memory_storage.query(
            MeteringQuery(start=start, end=end, limit=2, offset=2)
        )
        assert len(offset_results) == 2
        assert offset_results[0].task_id == "t2"


class TestInMemoryMeteringStorageAggregate:
    """Test InMemoryMeteringStorage aggregation."""

    @pytest.mark.asyncio
    async def test_aggregate_by_agent(
        self,
        in_memory_storage: InMemoryMeteringStorage,
        sample_metrics: UsageMetrics,
    ) -> None:
        """Aggregate by agent returns UsageAggregateByAgent."""
        await in_memory_storage.record(sample_metrics)
        await in_memory_storage.record(
            UsageMetrics(
                task_id="task_02",
                agent_id="agent_01",
                consumer_id="consumer_01",
                tokens_in=5,
                tokens_out=5,
                api_calls=2,
                timestamp=datetime(2026, 2, 17, 13, 0, 0, tzinfo=timezone.utc),
            )
        )
        aggs = await in_memory_storage.aggregate("agent")
        assert len(aggs) == 1
        assert aggs[0].agent_id == "agent_01"
        assert aggs[0].total_tokens == 10 + 20 + 5 + 5
        assert aggs[0].total_tasks == 2
        assert aggs[0].total_api_calls == 3

    @pytest.mark.asyncio
    async def test_aggregate_by_consumer(
        self,
        in_memory_storage: InMemoryMeteringStorage,
        sample_metrics: UsageMetrics,
    ) -> None:
        """Aggregate by consumer returns UsageAggregateByConsumer."""
        await in_memory_storage.record(sample_metrics)
        await in_memory_storage.record(
            UsageMetrics(
                task_id="task_02",
                agent_id="agent_02",
                consumer_id="consumer_01",
                tokens_in=1,
                tokens_out=1,
                timestamp=datetime(2026, 2, 17, 13, 0, 0, tzinfo=timezone.utc),
            )
        )
        aggs = await in_memory_storage.aggregate("consumer")
        assert len(aggs) == 1
        assert aggs[0].consumer_id == "consumer_01"
        assert aggs[0].total_tokens == 10 + 20 + 1 + 1
        assert aggs[0].total_tasks == 2

    @pytest.mark.asyncio
    async def test_aggregate_by_day(
        self,
        in_memory_storage: InMemoryMeteringStorage,
    ) -> None:
        """Aggregate by day groups by date."""
        await in_memory_storage.record(
            UsageMetrics(
                task_id="t1",
                agent_id="a1",
                consumer_id="c1",
                tokens_in=10,
                tokens_out=10,
                timestamp=datetime(2026, 2, 17, 10, 0, 0, tzinfo=timezone.utc),
            )
        )
        await in_memory_storage.record(
            UsageMetrics(
                task_id="t2",
                agent_id="a1",
                consumer_id="c1",
                tokens_in=20,
                tokens_out=20,
                timestamp=datetime(2026, 2, 17, 14, 0, 0, tzinfo=timezone.utc),
            )
        )
        await in_memory_storage.record(
            UsageMetrics(
                task_id="t3",
                agent_id="a1",
                consumer_id="c1",
                tokens_in=5,
                tokens_out=5,
                timestamp=datetime(2026, 2, 18, 10, 0, 0, tzinfo=timezone.utc),
            )
        )
        aggs = await in_memory_storage.aggregate("day")
        assert len(aggs) == 2
        by_period = {a.period: a for a in aggs}
        assert "2026-02-17" in by_period
        assert "2026-02-18" in by_period
        assert by_period["2026-02-17"].total_tokens == 60  # 10+10 + 20+20
        assert by_period["2026-02-18"].total_tokens == 10  # 5+5

    @pytest.mark.asyncio
    async def test_aggregate_with_filters_scopes_by_time_range(
        self,
        in_memory_storage: InMemoryMeteringStorage,
    ) -> None:
        """Aggregate with start/end filters only includes events in range."""
        await in_memory_storage.record(
            UsageMetrics(
                task_id="t1",
                agent_id="a1",
                consumer_id="c1",
                tokens_in=10,
                tokens_out=10,
                timestamp=datetime(2026, 2, 17, 12, 0, 0, tzinfo=timezone.utc),
            )
        )
        await in_memory_storage.record(
            UsageMetrics(
                task_id="t2",
                agent_id="a1",
                consumer_id="c1",
                tokens_in=20,
                tokens_out=20,
                timestamp=datetime(2026, 2, 19, 12, 0, 0, tzinfo=timezone.utc),
            )
        )
        start = datetime(2026, 2, 17, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 2, 18, 0, 0, 0, tzinfo=timezone.utc)
        filters = MeteringQuery(start=start, end=end)
        aggs = await in_memory_storage.aggregate("agent", filters=filters)
        assert len(aggs) == 1
        assert aggs[0].total_tokens == 20
        assert aggs[0].total_tasks == 1

    @pytest.mark.asyncio
    async def test_aggregate_invalid_group_by_raises(
        self,
        in_memory_storage: InMemoryMeteringStorage,
    ) -> None:
        """Aggregate with invalid group_by raises ValueError."""
        with pytest.raises(ValueError, match="group_by must be one of"):
            await in_memory_storage.aggregate("invalid")


class TestInMemoryMeteringStorageSummary:
    """Test InMemoryMeteringStorage summary."""

    @pytest.mark.asyncio
    async def test_summary_empty_returns_zeros(
        self,
        in_memory_storage: InMemoryMeteringStorage,
    ) -> None:
        """Summary with no events returns all zeros."""
        s = await in_memory_storage.summary()
        assert s.total_tasks == 0
        assert s.total_tokens == 0
        assert s.total_duration_ms == 0
        assert s.unique_agents == 0
        assert s.unique_consumers == 0
        assert s.total_api_calls == 0

    @pytest.mark.asyncio
    async def test_summary_aggregates_totals(
        self,
        in_memory_storage: InMemoryMeteringStorage,
        sample_metrics: UsageMetrics,
    ) -> None:
        """Summary returns total_tasks, total_tokens, total_duration_ms, etc."""
        await in_memory_storage.record(sample_metrics)
        await in_memory_storage.record(
            UsageMetrics(
                task_id="task_02",
                agent_id="agent_02",
                consumer_id="consumer_01",
                tokens_in=5,
                tokens_out=15,
                duration_ms=200,
                api_calls=2,
                timestamp=datetime(2026, 2, 17, 13, 0, 0, tzinfo=timezone.utc),
            )
        )
        s = await in_memory_storage.summary()
        assert s.total_tasks == 2
        assert s.total_tokens == 10 + 20 + 5 + 15
        assert s.total_duration_ms == 100 + 200
        assert s.unique_agents == 2
        assert s.unique_consumers == 1
        assert s.total_api_calls == 1 + 2

    @pytest.mark.asyncio
    async def test_summary_with_filters_scopes_by_time_range(
        self,
        in_memory_storage: InMemoryMeteringStorage,
    ) -> None:
        """Summary with start/end filters only includes events in range."""
        await in_memory_storage.record(
            UsageMetrics(
                task_id="t1",
                agent_id="a1",
                consumer_id="c1",
                tokens_in=10,
                tokens_out=20,
                duration_ms=100,
                timestamp=datetime(2026, 2, 17, 12, 0, 0, tzinfo=timezone.utc),
            )
        )
        await in_memory_storage.record(
            UsageMetrics(
                task_id="t2",
                agent_id="a2",
                consumer_id="c1",
                tokens_in=5,
                tokens_out=5,
                duration_ms=50,
                timestamp=datetime(2026, 2, 19, 12, 0, 0, tzinfo=timezone.utc),
            )
        )
        start = datetime(2026, 2, 17, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 2, 18, 0, 0, 0, tzinfo=timezone.utc)
        s = await in_memory_storage.summary(filters=MeteringQuery(start=start, end=end))
        assert s.total_tasks == 1
        assert s.total_tokens == 30
        assert s.total_duration_ms == 100
        assert s.unique_agents == 1
        assert s.unique_consumers == 1


class TestInMemoryMeteringStorageStats:
    """Test InMemoryMeteringStorage stats."""

    @pytest.mark.asyncio
    async def test_stats_empty_returns_zeros(
        self,
        in_memory_storage: InMemoryMeteringStorage,
    ) -> None:
        """Stats with no events returns total_events=0, oldest_timestamp=None."""
        s = await in_memory_storage.stats()
        assert s.total_events == 0
        assert s.oldest_timestamp is None
        assert s.retention_ttl_seconds is None

    @pytest.mark.asyncio
    async def test_stats_with_events_returns_totals(
        self,
        in_memory_storage: InMemoryMeteringStorage,
        sample_metrics: UsageMetrics,
    ) -> None:
        """Stats returns total_events and oldest_timestamp."""
        await in_memory_storage.record(sample_metrics)
        s = await in_memory_storage.stats()
        assert s.total_events == 1
        assert s.oldest_timestamp == sample_metrics.timestamp

    @pytest.mark.asyncio
    async def test_stats_with_retention_returns_ttl(
        self,
    ) -> None:
        """Stats returns retention_ttl_seconds when configured."""
        store = InMemoryMeteringStorage(retention_ttl_seconds=86400)
        s = await store.stats()
        assert s.retention_ttl_seconds == 86400


class TestInMemoryMeteringStorageRetention:
    """Test InMemoryMeteringStorage retention policy."""

    @pytest.mark.asyncio
    async def test_purge_expired_removes_old_events(self) -> None:
        """purge_expired removes events older than TTL."""
        store = InMemoryMeteringStorage(retention_ttl_seconds=3600)
        now = datetime.now(timezone.utc)
        old = now - timedelta(seconds=7200)
        await store.record(
            UsageMetrics(task_id="old", agent_id="a1", consumer_id="c1", timestamp=old)
        )
        await store.record(
            UsageMetrics(task_id="new", agent_id="a1", consumer_id="c1", timestamp=now)
        )
        removed = await store.purge_expired()
        assert removed == 1
        results = await store.query(MeteringQuery())
        assert len(results) == 1
        assert results[0].task_id == "new"

    @pytest.mark.asyncio
    async def test_purge_expired_no_retention_returns_zero(self) -> None:
        """purge_expired with no retention configured returns 0."""
        store = InMemoryMeteringStorage()
        await store.record(
            UsageMetrics(
                task_id="t1",
                agent_id="a1",
                consumer_id="c1",
                timestamp=datetime.now(timezone.utc),
            )
        )
        assert await store.purge_expired() == 0


class TestSQLiteMeteringStorage:
    """Test SQLiteMeteringStorage operations."""

    @pytest.mark.asyncio
    async def test_record_and_query(
        self,
        sqlite_storage: SQLiteMeteringStorage,
        sample_metrics: UsageMetrics,
    ) -> None:
        """Record then query returns metrics."""
        await sqlite_storage.record(sample_metrics)
        start = datetime(2026, 2, 17, 11, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 2, 17, 14, 0, 0, tzinfo=timezone.utc)
        results = await sqlite_storage.query(MeteringQuery(start=start, end=end))
        assert len(results) == 1
        assert results[0].task_id == sample_metrics.task_id
        assert results[0].tokens_in == 10

    @pytest.mark.asyncio
    async def test_aggregate_sums_metrics(
        self,
        sqlite_storage: SQLiteMeteringStorage,
        sample_metrics: UsageMetrics,
    ) -> None:
        """Aggregate returns correct totals."""
        await sqlite_storage.record(sample_metrics)
        await sqlite_storage.record(
            UsageMetrics(
                task_id="task_02",
                agent_id="agent_01",
                consumer_id="c1",
                tokens_in=5,
                tokens_out=5,
                api_calls=2,
                timestamp=datetime(2026, 2, 17, 13, 0, 0, tzinfo=timezone.utc),
            )
        )
        aggs = await sqlite_storage.aggregate("agent")
        assert len(aggs) == 1
        assert aggs[0].total_tokens == 40
        assert aggs[0].total_tasks == 2
        assert aggs[0].total_api_calls == 3

    @pytest.mark.asyncio
    async def test_query_empty_returns_empty_list(
        self,
        sqlite_storage: SQLiteMeteringStorage,
    ) -> None:
        """Query on empty store returns []."""
        results = await sqlite_storage.query(MeteringQuery())
        assert results == []

    @pytest.mark.asyncio
    async def test_aggregate_with_filters(
        self,
        sqlite_storage: SQLiteMeteringStorage,
    ) -> None:
        """SQLite aggregate with start/end filters scopes correctly."""
        await sqlite_storage.record(
            UsageMetrics(
                task_id="t1",
                agent_id="a1",
                consumer_id="c1",
                tokens_in=10,
                tokens_out=10,
                timestamp=datetime(2026, 2, 17, 12, 0, 0, tzinfo=timezone.utc),
            )
        )
        await sqlite_storage.record(
            UsageMetrics(
                task_id="t2",
                agent_id="a1",
                consumer_id="c1",
                tokens_in=20,
                tokens_out=20,
                timestamp=datetime(2026, 2, 19, 12, 0, 0, tzinfo=timezone.utc),
            )
        )
        start = datetime(2026, 2, 17, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 2, 18, 0, 0, 0, tzinfo=timezone.utc)
        filters = MeteringQuery(start=start, end=end)
        aggs = await sqlite_storage.aggregate("agent", filters=filters)
        assert len(aggs) == 1
        assert aggs[0].total_tokens == 20
        assert aggs[0].total_tasks == 1

    @pytest.mark.asyncio
    async def test_query_filters_by_task_id(
        self,
        sqlite_storage: SQLiteMeteringStorage,
        sample_metrics: UsageMetrics,
    ) -> None:
        """SQLite query filters by task_id."""
        await sqlite_storage.record(sample_metrics)
        await sqlite_storage.record(
            UsageMetrics(
                task_id="task_02",
                agent_id="agent_01",
                consumer_id="consumer_01",
                timestamp=datetime(2026, 2, 17, 13, 0, 0, tzinfo=timezone.utc),
            )
        )
        results = await sqlite_storage.query(MeteringQuery(task_id="task_01"))
        assert len(results) == 1
        assert results[0].task_id == "task_01"

    @pytest.mark.asyncio
    async def test_summary_returns_totals(
        self,
        sqlite_storage: SQLiteMeteringStorage,
        sample_metrics: UsageMetrics,
    ) -> None:
        """SQLite summary returns correct totals."""
        await sqlite_storage.record(sample_metrics)
        await sqlite_storage.record(
            UsageMetrics(
                task_id="task_02",
                agent_id="agent_02",
                consumer_id="consumer_01",
                tokens_in=5,
                tokens_out=15,
                duration_ms=200,
                api_calls=2,
                timestamp=datetime(2026, 2, 17, 13, 0, 0, tzinfo=timezone.utc),
            )
        )
        s = await sqlite_storage.summary()
        assert s.total_tasks == 2
        assert s.total_tokens == 50
        assert s.total_duration_ms == 300
        assert s.unique_agents == 2
        assert s.unique_consumers == 1
        assert s.total_api_calls == 3

    @pytest.mark.asyncio
    async def test_stats_returns_totals(
        self,
        sqlite_storage: SQLiteMeteringStorage,
        sample_metrics: UsageMetrics,
    ) -> None:
        """SQLite stats returns total_events and oldest_timestamp."""
        await sqlite_storage.record(sample_metrics)
        s = await sqlite_storage.stats()
        assert s.total_events == 1
        assert s.oldest_timestamp == sample_metrics.timestamp

    @pytest.mark.asyncio
    async def test_purge_expired_removes_old_events(
        self,
        tmp_path,
    ) -> None:
        """purge_expired removes events older than TTL."""
        store = SQLiteMeteringStorage(
            db_path=str(tmp_path / "purge_test.db"),
            retention_ttl_seconds=3600,
        )
        now = datetime.now(timezone.utc)
        old = now - timedelta(seconds=7200)
        await store.record(
            UsageMetrics(task_id="old", agent_id="a1", consumer_id="c1", timestamp=old)
        )
        await store.record(
            UsageMetrics(task_id="new", agent_id="a1", consumer_id="c1", timestamp=now)
        )
        removed = await store.purge_expired()
        assert removed == 1
        results = await store.query(MeteringQuery())
        assert len(results) == 1
        assert results[0].task_id == "new"


class TestPeriodToMeteringQuery:
    """Tests for _period_to_metering_query logic."""

    def test_hour_period(self) -> None:
        q = _period_to_metering_query("agent-1", "hour")
        assert q is not None
        assert q.agent_id == "agent-1"
        assert q.start is not None
        assert q.end is not None
        # Start should be roughly 1 hour ago
        diff = datetime.now(timezone.utc) - q.start
        assert timedelta(minutes=59) < diff < timedelta(minutes=61)

    def test_day_period(self) -> None:
        q = _period_to_metering_query("agent-1", "day")
        assert q is not None
        assert q.start is not None
        # Start should be beginning of today (00:00:00)
        now = datetime.now(timezone.utc)
        assert q.start.hour == 0
        assert q.start.minute == 0
        assert q.start.year == now.year
        assert q.start.month == now.month
        assert q.start.day == now.day

    def test_week_period(self) -> None:
        q = _period_to_metering_query("agent-1", "week")
        assert q is not None
        assert q.start is not None
        # Start should be roughly 7 days ago
        diff = datetime.now(timezone.utc) - q.start
        assert timedelta(days=6, hours=23) < diff < timedelta(days=7, hours=1)

    def test_unknown_period(self) -> None:
        q = _period_to_metering_query("agent-1", "unknown")
        # Should return query with just agent_id (no filtering by time)
        assert q is not None
        assert q.agent_id == "agent-1"
        assert q.start is None
        assert q.end is None

    @pytest.mark.parametrize("period", ["h", "d", "w", "today"])
    def test_period_shorthand_aliases(self, period: str) -> None:
        q = _period_to_metering_query("agent-1", period)
        assert q is not None
        assert q.agent_id == "agent-1"
        assert q.start is not None
        assert q.end is not None


# ---------------------------------------------------------------------------
# Helper for coverage tests
# ---------------------------------------------------------------------------


def _metric(
    task_id: str = "t1",
    agent_id: str = "a1",
    consumer_id: str = "c1",
    timestamp: datetime | None = None,
    tokens_in: int = 10,
    tokens_out: int = 20,
    duration_ms: int = 100,
    api_calls: int = 1,
) -> UsageMetrics:
    return UsageMetrics(
        task_id=task_id,
        agent_id=agent_id,
        consumer_id=consumer_id,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        duration_ms=duration_ms,
        api_calls=api_calls,
        timestamp=timestamp or datetime(2026, 2, 17, 12, 0, 0, tzinfo=timezone.utc),
    )


# ---------------------------------------------------------------------------
# MeteringStorageBridge over the economics backends
# ---------------------------------------------------------------------------


class TestMeteringStorageAdapter:
    """Cover the ``MeteringStore`` view that ``MeteringStorageBridge`` gives each backend."""

    @pytest.mark.asyncio
    async def test_adapter_record_and_query(self) -> None:
        """Adapter record() converts UsageEvent; query() returns UsageEvents."""
        from asap.state.metering import UsageEvent
        from asap.state.metering import UsageMetrics as StateUsageMetrics

        backend = InMemoryMeteringStorage()
        adapter = MeteringStorageBridge(backend)

        event = UsageEvent(
            task_id="t1",
            agent_id="a1",
            consumer_id="c1",
            metrics=StateUsageMetrics(
                tokens_in=10,
                tokens_out=20,
                duration_ms=100,
                api_calls=1,
            ),
            timestamp=datetime(2026, 2, 17, 12, 0, 0, tzinfo=timezone.utc),
        )
        await adapter.record(event)

        results = await adapter.query(
            agent_id="a1",
            start=datetime(2026, 2, 17, 0, 0, 0, tzinfo=timezone.utc),
            end=datetime(2026, 2, 18, 0, 0, 0, tzinfo=timezone.utc),
        )
        assert len(results) == 1
        assert results[0].task_id == "t1"
        assert results[0].metrics.tokens_in == 10

    @pytest.mark.asyncio
    async def test_adapter_aggregate_matching_agent(self) -> None:
        """Adapter aggregate() returns StateUsageAggregate for matching agent."""
        backend = InMemoryMeteringStorage()
        adapter = MeteringStorageBridge(backend)

        now = datetime.now(timezone.utc)
        m = _metric(agent_id="a1", tokens_in=10, tokens_out=20, api_calls=3, timestamp=now)
        await backend.record(m)

        agg = await adapter.aggregate(agent_id="a1", period="hour")
        assert agg.agent_id == "a1"
        assert agg.total_tokens == 30
        assert agg.total_api_calls == 3

    @pytest.mark.asyncio
    async def test_adapter_aggregate_no_matching_agent(self) -> None:
        """Adapter aggregate() returns empty StateUsageAggregate when no data."""
        backend = InMemoryMeteringStorage()
        adapter = MeteringStorageBridge(backend)

        agg = await adapter.aggregate(agent_id="nonexistent", period="day")
        assert agg.agent_id == "nonexistent"
        assert agg.total_tokens == 0

    @pytest.mark.asyncio
    async def test_adapter_aggregate_unknown_period(self) -> None:
        """Adapter aggregate() with unknown period returns data (no time filter)."""
        backend = InMemoryMeteringStorage()
        adapter = MeteringStorageBridge(backend)

        await backend.record(_metric(agent_id="a1"))
        agg = await adapter.aggregate(agent_id="a1", period="all")
        assert agg.agent_id == "a1"
        assert agg.total_tokens == 30

    @pytest.mark.asyncio
    async def test_sqlite_adapter_aggregate_period_h_excludes_old_events(
        self, sqlite_storage: SQLiteMeteringStorage
    ) -> None:
        """Adapter aggregate(period='h') on SQLite excludes events older than one hour."""
        adapter = MeteringStorageBridge(sqlite_storage)
        now = datetime.now(timezone.utc)

        await sqlite_storage.record(
            _metric(
                task_id="old",
                agent_id="a1",
                tokens_in=100,
                tokens_out=100,
                timestamp=now - timedelta(hours=2),
            )
        )
        await sqlite_storage.record(
            _metric(
                task_id="recent",
                agent_id="a1",
                tokens_in=5,
                tokens_out=10,
                timestamp=now - timedelta(minutes=30),
            )
        )

        agg = await adapter.aggregate(agent_id="a1", period="h")

        assert agg.agent_id == "a1"
        assert agg.total_tokens == 15
        assert agg.total_api_calls == 1


# ---------------------------------------------------------------------------
# SQLiteMeteringStorage — additional coverage
# ---------------------------------------------------------------------------


class TestSQLiteMeteringStorageCoverage:
    """Cover SQLite paths not hit by existing tests."""

    @pytest.mark.asyncio
    async def test_aggregate_without_filters_consumer(
        self, sqlite_storage: SQLiteMeteringStorage
    ) -> None:
        """aggregate('consumer') without filters uses full-table scan path."""
        await sqlite_storage.record(_metric(consumer_id="c1"))
        await sqlite_storage.record(
            _metric(task_id="t2", consumer_id="c2", tokens_in=5, tokens_out=5)
        )
        aggs = await sqlite_storage.aggregate("consumer")
        assert len(aggs) == 2
        by_consumer = {a.consumer_id: a for a in aggs}
        assert by_consumer["c1"].total_tokens == 30
        assert by_consumer["c2"].total_tokens == 10

    @pytest.mark.asyncio
    async def test_aggregate_without_filters_day(
        self, sqlite_storage: SQLiteMeteringStorage
    ) -> None:
        """aggregate('day') without filters groups by date."""
        await sqlite_storage.record(
            _metric(timestamp=datetime(2026, 2, 17, 10, 0, 0, tzinfo=timezone.utc))
        )
        await sqlite_storage.record(
            _metric(
                task_id="t2",
                timestamp=datetime(2026, 2, 18, 10, 0, 0, tzinfo=timezone.utc),
                tokens_in=5,
                tokens_out=5,
            )
        )
        aggs = await sqlite_storage.aggregate("day")
        assert len(aggs) == 2

    @pytest.mark.asyncio
    async def test_aggregate_without_filters_week(
        self, sqlite_storage: SQLiteMeteringStorage
    ) -> None:
        """aggregate('week') without filters groups by week."""
        await sqlite_storage.record(
            _metric(timestamp=datetime(2026, 2, 17, 10, 0, 0, tzinfo=timezone.utc))
        )
        aggs = await sqlite_storage.aggregate("week")
        assert len(aggs) >= 1

    @pytest.mark.asyncio
    async def test_aggregate_invalid_group_by_raises(
        self, sqlite_storage: SQLiteMeteringStorage
    ) -> None:
        """aggregate() with invalid group_by raises ValueError."""
        with pytest.raises(ValueError, match="group_by must be one of"):
            await sqlite_storage.aggregate("invalid")

    @pytest.mark.asyncio
    async def test_summary_without_filters(self, sqlite_storage: SQLiteMeteringStorage) -> None:
        """summary() without filters uses full-table scan path."""
        await sqlite_storage.record(_metric())
        s = await sqlite_storage.summary()
        assert s.total_tasks == 1
        assert s.total_tokens == 30

    @pytest.mark.asyncio
    async def test_summary_with_filters(self, sqlite_storage: SQLiteMeteringStorage) -> None:
        """summary() with filters scopes correctly."""
        await sqlite_storage.record(
            _metric(timestamp=datetime(2026, 2, 17, 12, 0, 0, tzinfo=timezone.utc))
        )
        await sqlite_storage.record(
            _metric(
                task_id="t2",
                timestamp=datetime(2026, 2, 19, 12, 0, 0, tzinfo=timezone.utc),
            )
        )
        s = await sqlite_storage.summary(
            filters=MeteringQuery(
                start=datetime(2026, 2, 17, 0, 0, 0, tzinfo=timezone.utc),
                end=datetime(2026, 2, 18, 0, 0, 0, tzinfo=timezone.utc),
            )
        )
        assert s.total_tasks == 1

    @pytest.mark.asyncio
    async def test_stats_empty(self, sqlite_storage: SQLiteMeteringStorage) -> None:
        """stats() on empty table returns total_events=0."""
        s = await sqlite_storage.stats()
        assert s.total_events == 0
        assert s.oldest_timestamp is None

    @pytest.mark.asyncio
    async def test_purge_no_retention(self, sqlite_storage: SQLiteMeteringStorage) -> None:
        """purge_expired() with no retention configured returns 0."""
        await sqlite_storage.record(_metric())
        removed = await sqlite_storage.purge_expired()
        assert removed == 0

    @pytest.mark.asyncio
    async def test_query_rejects_non_allowlisted_where_fragments(
        self,
        sqlite_storage: SQLiteMeteringStorage,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Fail-closed guard rejects dynamic SQL fragments outside the allowlist."""
        monkeypatch.setattr(SQLiteMeteringStorage, "_ALLOWED_QUERY_FRAGMENTS", frozenset())
        with pytest.raises(ValueError, match="unexpected WHERE fragment"):
            await sqlite_storage.query(MeteringQuery(agent_id="agent_01"))
