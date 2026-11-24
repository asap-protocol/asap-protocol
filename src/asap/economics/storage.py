"""Metering storage: record, query, aggregate usage metrics (v1.3).

**Architecture:** Two metering layers exist:
- **Economics layer** (this module): ``MeteringStorage`` protocol with full CRUD,
  aggregation, summary, stats, purge. Used by the Usage REST API and as the
  backend for the bridge.
- **State layer** (``asap.state.metering``): ``MeteringStore`` protocol with
  minimal record/query/aggregate. Used by handlers for task completion recording.
  ``asap.state.metering.MeteringStorageBridge`` bridges economics -> state so a
  single MeteringStorage can serve both the API and handlers.
"""

from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol, Union, cast, runtime_checkable

from pydantic import Field

from asap.economics.metering import (
    StorageStats,
    UsageAggregateByAgent,
    UsageAggregateByConsumer,
    UsageAggregateByPeriod,
    UsageMetrics,
    UsageSummary,
)
from asap.models.base import ASAPBaseModel
from asap.models.ids import generate_id

# State is the lower layer (never imports economics), so importing the shared
# usage_events DDL + repository base here cannot form a cycle. One canonical DDL
# owner prevents divergent indexes when both stores share asap_state.db; the
# shared base owns WAL pragmas, ``:memory:`` handling, and idempotent schema init.
from asap.state.stores import DEFAULT_DB_PATH, AsyncSqliteRepository, build_where, parse_iso
from asap.state.stores.sqlite import _USAGE_EVENTS_DDL

UsageAggregate = Union[
    UsageAggregateByAgent,
    UsageAggregateByConsumer,
    UsageAggregateByPeriod,
]


_VALID_GROUP_BY = ("agent", "consumer", "day", "week")


def _dispatch_aggregate(events: list[UsageMetrics], group_by: str) -> list[UsageAggregate]:
    """Dispatch aggregation by ``group_by`` and widen the concrete result to ``UsageAggregate``.

    Python's invariant ``list`` means a ``list[UsageAggregateByAgent]`` is not assignable to
    ``list[UsageAggregate]`` even though the element types are compatible; the single ``cast``
    centralized here is what the type system needs. Invalid ``group_by`` raises ``ValueError``.
    """
    if group_by == "agent":
        return cast("list[UsageAggregate]", _aggregate_by_agent(events))
    if group_by == "consumer":
        return cast("list[UsageAggregate]", _aggregate_by_consumer(events))
    if group_by in ("day", "week"):
        return cast("list[UsageAggregate]", _aggregate_by_period(events, group_by))
    raise ValueError(f"group_by must be one of {_VALID_GROUP_BY!r}; got {group_by!r}")


class MeteringQuery(ASAPBaseModel):
    agent_id: str | None = Field(default=None, description="Filter by agent")
    consumer_id: str | None = Field(default=None, description="Filter by consumer")
    task_id: str | None = Field(default=None, description="Filter by task")
    start: datetime | None = Field(default=None, description="Start of time range")
    end: datetime | None = Field(default=None, description="End of time range")
    limit: int | None = Field(default=None, ge=1, description="Max events to return")
    offset: int = Field(default=0, ge=0, description="Events to skip")


@runtime_checkable
class MeteringStorage(Protocol):
    async def record(self, metrics: UsageMetrics) -> None: ...
    async def query(self, filters: MeteringQuery) -> list[UsageMetrics]: ...
    async def aggregate(
        self,
        group_by: str,
        filters: MeteringQuery | None = None,
    ) -> list[UsageAggregate]: ...
    async def summary(self, filters: MeteringQuery | None = None) -> UsageSummary: ...
    async def stats(self) -> StorageStats: ...
    async def purge_expired(self) -> int: ...


class MeteringStorageBase(ABC):
    @abstractmethod
    async def record(self, metrics: UsageMetrics) -> None: ...

    @abstractmethod
    async def query(self, filters: MeteringQuery) -> list[UsageMetrics]: ...

    @abstractmethod
    async def aggregate(
        self,
        group_by: str,
        filters: MeteringQuery | None = None,
    ) -> list[UsageAggregate]: ...

    @abstractmethod
    async def summary(self, filters: MeteringQuery | None = None) -> UsageSummary: ...

    @abstractmethod
    async def stats(self) -> StorageStats: ...

    @abstractmethod
    async def purge_expired(self) -> int: ...


def _matches(e: UsageMetrics, f: MeteringQuery) -> bool:
    return (
        (f.agent_id is None or e.agent_id == f.agent_id)
        and (f.consumer_id is None or e.consumer_id == f.consumer_id)
        and (f.task_id is None or e.task_id == f.task_id)
        and (f.start is None or e.timestamp >= f.start)
        and (f.end is None or e.timestamp <= f.end)
    )


def _apply_query_filters(
    events: list[UsageMetrics],
    filters: MeteringQuery,
) -> list[UsageMetrics]:
    filtered = sorted((e for e in events if _matches(e, filters)), key=lambda x: x.timestamp)
    out = filtered[filters.offset :]
    return out[: filters.limit] if filters.limit else out


def _aggregate_by_agent(events: list[UsageMetrics]) -> list[UsageAggregateByAgent]:
    """Aggregate usage by agent_id."""
    by_agent: dict[str, list[UsageMetrics]] = {}
    for e in events:
        by_agent.setdefault(e.agent_id, []).append(e)
    out: list[UsageAggregateByAgent] = []
    for agent_id, agent_events in by_agent.items():
        total_tokens = sum(e.tokens_in + e.tokens_out for e in agent_events)
        total_duration = sum(e.duration_ms for e in agent_events)
        total_tasks = len(agent_events)
        total_api_calls = sum(e.api_calls for e in agent_events)
        avg_tokens = total_tokens / total_tasks if total_tasks else 0.0
        avg_duration = total_duration / total_tasks if total_tasks else 0.0
        out.append(
            UsageAggregateByAgent(
                agent_id=agent_id,
                period="all",
                total_tokens=total_tokens,
                total_duration_ms=total_duration,
                total_tasks=total_tasks,
                total_api_calls=total_api_calls,
                avg_tokens_per_task=avg_tokens,
                avg_duration_ms_per_task=avg_duration,
            )
        )
    return sorted(out, key=lambda a: a.agent_id)


def _aggregate_by_consumer(events: list[UsageMetrics]) -> list[UsageAggregateByConsumer]:
    """Aggregate usage by consumer_id."""
    by_consumer: dict[str, list[UsageMetrics]] = {}
    for e in events:
        by_consumer.setdefault(e.consumer_id, []).append(e)
    out: list[UsageAggregateByConsumer] = []
    for consumer_id, consumer_events in by_consumer.items():
        total_tokens = sum(e.tokens_in + e.tokens_out for e in consumer_events)
        total_duration = sum(e.duration_ms for e in consumer_events)
        total_tasks = len(consumer_events)
        total_api_calls = sum(e.api_calls for e in consumer_events)
        avg_tokens = total_tokens / total_tasks if total_tasks else 0.0
        out.append(
            UsageAggregateByConsumer(
                consumer_id=consumer_id,
                period="all",
                total_tokens=total_tokens,
                total_duration_ms=total_duration,
                total_tasks=total_tasks,
                total_api_calls=total_api_calls,
                avg_tokens_per_task=avg_tokens,
            )
        )
    return sorted(out, key=lambda a: a.consumer_id)


def _compute_summary(events: list[UsageMetrics]) -> UsageSummary:
    """Compute UsageSummary from a list of events."""
    if not events:
        return UsageSummary()
    total_tokens = sum(e.tokens_in + e.tokens_out for e in events)
    total_duration = sum(e.duration_ms for e in events)
    total_tasks = len(events)
    total_api_calls = sum(e.api_calls for e in events)
    unique_agents = len({e.agent_id for e in events})
    unique_consumers = len({e.consumer_id for e in events})
    return UsageSummary(
        total_tasks=total_tasks,
        total_tokens=total_tokens,
        total_duration_ms=total_duration,
        unique_agents=unique_agents,
        unique_consumers=unique_consumers,
        total_api_calls=total_api_calls,
    )


def _aggregate_by_period(
    events: list[UsageMetrics],
    period_format: str,
) -> list[UsageAggregateByPeriod]:
    """Aggregate usage by time period (day or week)."""

    def _period_key(ts: datetime) -> str:
        d = ts.date()
        if period_format == "day":
            return d.isoformat()
        iso = d.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"

    by_period: dict[str, list[UsageMetrics]] = {}
    for e in events:
        key = _period_key(e.timestamp)
        by_period.setdefault(key, []).append(e)

    out: list[UsageAggregateByPeriod] = []
    for period, period_events in by_period.items():
        total_tokens = sum(e.tokens_in + e.tokens_out for e in period_events)
        total_duration = sum(e.duration_ms for e in period_events)
        total_tasks = len(period_events)
        total_api_calls = sum(e.api_calls for e in period_events)
        unique_agents = len({e.agent_id for e in period_events})
        unique_consumers = len({e.consumer_id for e in period_events})
        out.append(
            UsageAggregateByPeriod(
                period=period,
                total_tokens=total_tokens,
                total_duration_ms=total_duration,
                total_tasks=total_tasks,
                total_api_calls=total_api_calls,
                unique_agents=unique_agents,
                unique_consumers=unique_consumers,
            )
        )
    return sorted(out, key=lambda a: a.period)


class InMemoryMeteringStorage(MeteringStorageBase):
    """In-memory MeteringStorage for development and testing.

    Stores UsageMetrics in a list. Not persistent across restarts.
    Async-safe via asyncio.Lock. Optional retention_ttl_seconds for auto-cleanup.
    """

    def __init__(self, retention_ttl_seconds: int | None = None) -> None:
        self._lock = asyncio.Lock()
        self._events: list[UsageMetrics] = []
        self._retention_ttl_seconds = retention_ttl_seconds

    async def record(self, metrics: UsageMetrics) -> None:
        """Append a usage event (async-safe)."""
        async with self._lock:
            self._events.append(metrics)

    async def query(self, filters: MeteringQuery) -> list[UsageMetrics]:
        """Return events matching filters, sorted by timestamp."""
        async with self._lock:
            return _apply_query_filters(list(self._events), filters)

    async def aggregate(
        self,
        group_by: str,
        filters: MeteringQuery | None = None,
    ) -> list[UsageAggregate]:
        """Aggregate by agent, consumer, day, or week, optionally filtered."""
        async with self._lock:
            events = list(self._events)
        if filters is not None:
            agg_filters = MeteringQuery(
                agent_id=filters.agent_id,
                consumer_id=filters.consumer_id,
                task_id=filters.task_id,
                start=filters.start,
                end=filters.end,
                limit=None,
                offset=0,
            )
            events = _apply_query_filters(events, agg_filters)
        return _dispatch_aggregate(events, group_by)

    async def summary(self, filters: MeteringQuery | None = None) -> UsageSummary:
        """Return dashboard summary, optionally filtered."""
        async with self._lock:
            events = list(self._events)
        if filters is not None:
            agg_filters = MeteringQuery(
                agent_id=filters.agent_id,
                consumer_id=filters.consumer_id,
                task_id=filters.task_id,
                start=filters.start,
                end=filters.end,
                limit=None,
                offset=0,
            )
            events = _apply_query_filters(events, agg_filters)
        return _compute_summary(events)

    async def stats(self) -> StorageStats:
        """Return storage statistics."""
        async with self._lock:
            events = list(self._events)
        if not events:
            return StorageStats(
                total_events=0,
                oldest_timestamp=None,
                retention_ttl_seconds=self._retention_ttl_seconds,
            )
        oldest = min(e.timestamp for e in events)
        return StorageStats(
            total_events=len(events),
            oldest_timestamp=oldest,
            retention_ttl_seconds=self._retention_ttl_seconds,
        )

    async def purge_expired(self) -> int:
        """Remove events older than retention_ttl_seconds. Returns count removed."""
        if self._retention_ttl_seconds is None:
            return 0
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=self._retention_ttl_seconds)
        cutoff = cutoff.replace(microsecond=0)
        async with self._lock:
            before = len(self._events)
            self._events = [e for e in self._events if e.timestamp >= cutoff]
            return before - len(self._events)


# Compat shim: delegates to ``asap.state.metering._period_to_metering_query``.
def _period_to_metering_query(agent_id: str, period: str) -> MeteringQuery | None:
    """Convert a period string to a ``MeteringQuery`` (delegates to the state helper)."""
    from asap.state.metering import _period_to_metering_query as _state_helper

    return _state_helper(agent_id, period)


# MeteringQuery field -> usage_events WHERE fragment. Values are bound via
# ``build_where`` params, never interpolated. ``_ALLOWED_QUERY_FRAGMENTS`` is
# the fail-closed guard tests monkeypatch to verify no fragment leaks.
_USAGE_EVENTS_WHERE: dict[str, str] = {
    "agent_id": "agent_id = ?",
    "consumer_id": "consumer_id = ?",
    "task_id": "task_id = ?",
    "start": "timestamp >= ?",
    "end": "timestamp <= ?",
}

# SELECT projection for usage_events (shared by query/aggregate/summary full scans).
_USAGE_EVENTS_SELECT = (
    "SELECT id, task_id, agent_id, consumer_id, metrics, timestamp FROM usage_events"
)


def _metrics_to_row(metrics: UsageMetrics, event_id: str) -> tuple[str, str, str, str, str, str]:
    """Serialize UsageMetrics to DB row (same schema as state UsageEvent)."""
    metrics_json = json.dumps(
        {
            "tokens_in": metrics.tokens_in,
            "tokens_out": metrics.tokens_out,
            "duration_ms": metrics.duration_ms,
            "api_calls": metrics.api_calls,
        }
    )
    ts = metrics.timestamp.isoformat()
    return (
        event_id,
        metrics.task_id,
        metrics.agent_id,
        metrics.consumer_id,
        metrics_json,
        ts,
    )


def _row_to_metrics(row: tuple[Any, ...]) -> UsageMetrics:
    """Build UsageMetrics from DB row."""
    _id, task_id, agent_id, consumer_id, metrics_json, ts_str = row
    data = json.loads(metrics_json)
    ts = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
    return UsageMetrics(
        task_id=task_id,
        agent_id=agent_id,
        consumer_id=consumer_id,
        tokens_in=data.get("tokens_in", 0),
        tokens_out=data.get("tokens_out", 0),
        duration_ms=data.get("duration_ms", 0),
        api_calls=data.get("api_calls", 0),
        timestamp=ts,
    )


def _filters_to_where(filters: MeteringQuery) -> tuple[str, list[Any]]:
    """Map a ``MeteringQuery`` to a WHERE clause via the base allow-list builder.

    Datetimes are isoformatted for SQLite TEXT comparison. ``limit``/``offset``
    are appended separately by callers (they are not WHERE predicates).
    """
    return build_where(
        {
            "agent_id": filters.agent_id,
            "consumer_id": filters.consumer_id,
            "task_id": filters.task_id,
            "start": filters.start.isoformat() if filters.start else None,
            "end": filters.end.isoformat() if filters.end else None,
        },
        _USAGE_EVENTS_WHERE,
    )


class SQLiteMeteringStorage(AsyncSqliteRepository, MeteringStorageBase):
    """SQLite-backed MeteringStorage; usage events persist across restarts.

    Subclasses :class:`AsyncSqliteRepository` so aiosqlite plumbing, WAL pragmas,
    the ``:memory:`` persistent connection, and idempotent schema init are owned
    by the shared base. Uses the same ``usage_events`` table as the state
    ``SQLiteMeteringStore`` (canonical DDL via ``_USAGE_EVENTS_DDL``) for physical
    compatibility. Optional ``retention_ttl_seconds`` for configurable TTL; call
    ``purge_expired()`` periodically to remove old data.
    """

    def __init__(
        self,
        db_path: str | Path = DEFAULT_DB_PATH,
        retention_ttl_seconds: int | None = None,
    ) -> None:
        super().__init__(db_path, schema_ddl=_USAGE_EVENTS_DDL)
        self._retention_ttl_seconds = retention_ttl_seconds

    # Fail-closed guard: tests monkeypatch this to ``frozenset()`` to verify
    # ``_query_impl`` rejects any WHERE fragment not in the allow-list.
    _ALLOWED_QUERY_FRAGMENTS: frozenset[str] = frozenset(_USAGE_EVENTS_WHERE.values())

    async def _record_impl(self, metrics: UsageMetrics) -> None:
        """Insert one usage event row."""
        event_id = f"evt_{generate_id()}"
        row = _metrics_to_row(metrics, event_id)
        await self.execute(
            """
            INSERT INTO usage_events
            (id, task_id, agent_id, consumer_id, metrics, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            row,
        )

    async def _query_impl(self, filters: MeteringQuery) -> list[UsageMetrics]:
        """Select events matching ``filters`` via the base allow-list WHERE builder."""
        # Fail-closed: derive the emitted fragments from the non-None filter keys
        # (rather than splitting the assembled WHERE string, which would be brittle
        # if a fragment ever embedded " AND "). Tests empty _ALLOWED_QUERY_FRAGMENTS
        # to verify the guard rejects any fragment outside the allow-list.
        active_keys = [
            key
            for key in ("agent_id", "consumer_id", "task_id", "start", "end")
            if getattr(filters, key) is not None
        ]
        emitted = [_USAGE_EVENTS_WHERE[key] for key in active_keys]
        if not all(f in self._ALLOWED_QUERY_FRAGMENTS for f in emitted):
            raise ValueError("unexpected WHERE fragment")
        where, params = _filters_to_where(filters)
        sql = f"""
            {_USAGE_EVENTS_SELECT}
            WHERE {where}
            ORDER BY timestamp
            LIMIT ? OFFSET ?
        """  # nosec B608 - ``where`` is assembled by build_where from _USAGE_EVENTS_WHERE; values parameterized
        limit_val = filters.limit if filters.limit is not None else -1
        params.extend([limit_val, filters.offset])
        rows = await self.fetch_all(sql, tuple(params))
        return [_row_to_metrics(r) for r in rows]

    async def _fetch_all_events(self) -> list[UsageMetrics]:
        """Read every usage_events row (full-table scan path for unfiltered aggregate/summary)."""
        rows = await self.fetch_all(f"{_USAGE_EVENTS_SELECT} ORDER BY timestamp")  # nosec B608 - static SQL
        return [_row_to_metrics(r) for r in rows]

    async def _aggregate_impl(
        self,
        group_by: str,
        filters: MeteringQuery | None = None,
    ) -> list[UsageAggregate]:
        if filters is not None:
            agg_filters = MeteringQuery(
                agent_id=filters.agent_id,
                consumer_id=filters.consumer_id,
                task_id=filters.task_id,
                start=filters.start,
                end=filters.end,
                limit=None,
                offset=0,
            )
            events = await self._query_impl(agg_filters)
        else:
            events = await self._fetch_all_events()
        return _dispatch_aggregate(events, group_by)

    async def record(self, metrics: UsageMetrics) -> None:
        """Record a usage event."""
        await self._record_impl(metrics)

    async def query(self, filters: MeteringQuery) -> list[UsageMetrics]:
        """Query events with filters."""
        return await self._query_impl(filters)

    async def aggregate(
        self,
        group_by: str,
        filters: MeteringQuery | None = None,
    ) -> list[UsageAggregate]:
        """Aggregate by agent, consumer, day, or week."""
        return await self._aggregate_impl(group_by, filters)

    async def _summary_impl(self, filters: MeteringQuery | None = None) -> UsageSummary:
        """Compute summary from events, optionally filtered."""
        if filters is not None:
            summary_filters = MeteringQuery(
                agent_id=filters.agent_id,
                consumer_id=filters.consumer_id,
                task_id=filters.task_id,
                start=filters.start,
                end=filters.end,
                limit=None,
                offset=0,
            )
            events = await self._query_impl(summary_filters)
        else:
            events = await self._fetch_all_events()
        return _compute_summary(events)

    async def summary(self, filters: MeteringQuery | None = None) -> UsageSummary:
        """Return dashboard summary."""
        return await self._summary_impl(filters)

    async def _stats_impl(self) -> StorageStats:
        """Compute storage stats from SQLite."""
        row = await self.fetch_one("SELECT COUNT(*), MIN(timestamp) FROM usage_events")
        count = row[0] if row and row[0] is not None else 0
        oldest_ts = parse_iso(str(row[1])) if row and row[1] is not None else None
        return StorageStats(
            total_events=count,
            oldest_timestamp=oldest_ts,
            retention_ttl_seconds=self._retention_ttl_seconds,
        )

    async def stats(self) -> StorageStats:
        """Return storage statistics."""
        return await self._stats_impl()

    async def _purge_expired_impl(self) -> int:
        """Delete events older than retention_ttl_seconds. Returns count removed."""
        if self._retention_ttl_seconds is None:
            return 0
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=self._retention_ttl_seconds)
        cutoff = cutoff.replace(microsecond=0)
        return await self.execute(
            "DELETE FROM usage_events WHERE timestamp < ?",
            (cutoff.isoformat(),),
        )

    async def purge_expired(self) -> int:
        """Remove events older than retention_ttl_seconds. Returns count removed."""
        return await self._purge_expired_impl()
