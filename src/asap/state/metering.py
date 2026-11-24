"""ASAP Metering Store for usage and economics (v1.3 foundation).

This module defines the abstract interface and models for storing usage metering
data, enabling the v1.3 Economics Layer (METER-001 to METER-006).

Example:
    >>> store = InMemoryMeteringStore()
    >>> isinstance(store, AsyncMeteringStore)
    True
"""

from __future__ import annotations

import asyncio
import warnings
from collections.abc import Awaitable
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pydantic import Field

from asap.models.base import ASAPBaseModel

if TYPE_CHECKING:
    # Economics is the higher layer; importing it at runtime from the foundation
    # layer would form a cycle (economics.storage imports state.stores). The
    # metering_storage bridge below duck-types the MeteringStorage protocol at
    # runtime and only needs the type for annotations.
    from asap.economics.storage import MeteringQuery, MeteringStorage


class UsageMetrics(ASAPBaseModel):
    """Metrics for a single usage event (tokens, duration, API calls).

    Attributes:
        tokens_in: Input token count.
        tokens_out: Output token count.
        duration_ms: Duration in milliseconds.
        api_calls: Number of API calls.
    """

    tokens_in: int = Field(default=0, ge=0, description="Input token count")
    tokens_out: int = Field(default=0, ge=0, description="Output token count")
    duration_ms: int = Field(default=0, ge=0, description="Duration in milliseconds")
    api_calls: int = Field(default=0, ge=0, description="Number of API calls")


class UsageEvent(ASAPBaseModel):
    """A single usage event for metering (v1.3 foundation).

    Attributes:
        task_id: Task identifier.
        agent_id: Agent that produced the usage.
        consumer_id: Consumer (caller) identifier.
        metrics: Token, duration and API call metrics.
        timestamp: When the event occurred (UTC).
    """

    task_id: str = Field(..., description="Task identifier")
    agent_id: str = Field(..., description="Agent that produced the usage")
    consumer_id: str = Field(..., description="Consumer (caller) identifier")
    metrics: UsageMetrics = Field(
        default_factory=UsageMetrics,
        description="Token, duration and API call metrics",
    )
    timestamp: datetime = Field(..., description="Event timestamp (UTC)")


class UsageAggregate(ASAPBaseModel):
    """Aggregated usage for an agent over a period.

    Attributes:
        agent_id: Agent identifier.
        period: Aggregation period (e.g. 'hour', 'day').
        total_tokens: Sum of input + output tokens.
        total_duration: Total duration in milliseconds.
        total_tasks: Number of tasks.
        total_api_calls: Total API calls.
    """

    agent_id: str = Field(..., description="Agent identifier")
    period: str = Field(..., description="Aggregation period (e.g. hour, day)")
    total_tokens: int = Field(default=0, ge=0, description="Sum of tokens (in + out)")
    total_duration: int = Field(default=0, ge=0, description="Total duration in ms")
    total_tasks: int = Field(default=0, ge=0, description="Number of tasks")
    total_api_calls: int = Field(default=0, ge=0, description="Total API calls")


@runtime_checkable
class AsyncMeteringStore(Protocol):
    """Async usage metering storage (:class:`MeteringStore` as ``async def``)."""

    async def record(self, event: UsageEvent) -> None:
        """Record a usage event."""
        ...

    async def query(
        self,
        agent_id: str,
        start: datetime,
        end: datetime,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[UsageEvent]:
        """Query usage events for an agent in a time range."""
        ...

    async def aggregate(self, agent_id: str, period: str) -> UsageAggregate:
        """Aggregate usage for an agent over a period."""
        ...


@runtime_checkable
@warnings.deprecated(
    "Prefer AsyncMeteringStore for new code; this protocol keeps the older "
    "Awaitable-return style for gradual migration.",
    category=DeprecationWarning,
)
class MeteringStore(Protocol):
    """Protocol for usage metering storage implementations.

    Provides the interface for recording and querying usage events and
    aggregates. Implementations can use various backends (memory, SQLite, etc.).
    Foundation for v1.3 Usage Metering.

    Example:
        >>> class CustomMeteringStore:
        ...     async def record(self, event: UsageEvent) -> None: ...
        ...     async def query(self, agent_id: str, start: datetime, end: datetime) -> list[UsageEvent]: ...
        ...     async def aggregate(self, agent_id: str, period: str) -> UsageAggregate: ...
        >>> isinstance(CustomMeteringStore(), MeteringStore)
        True
    """

    def record(self, event: "UsageEvent") -> "Awaitable[None]":
        """Record a usage event.

        Args:
            event: The usage event to store.

        Example:
            >>> store = InMemoryMeteringStore()
            >>> event = UsageEvent(task_id="task_01", agent_id="agent_01", ...)
            >>> store.record(event)
        """
        ...

    def query(
        self,
        agent_id: str,
        start: datetime,
        end: datetime,
        limit: int | None = None,
        offset: int = 0,
    ) -> Awaitable[list["UsageEvent"]]:
        """Query usage events for an agent in a time range.

        Args:
            agent_id: Agent identifier.
            start: Start of the time range (inclusive).
            end: End of the time range (inclusive).
            limit: Maximum number of events to return (None = no limit).
            offset: Number of events to skip (default 0).

        Returns:
            List of usage events in the range, ordered by timestamp.

        Example:
            >>> store = InMemoryMeteringStore()
            >>> events = store.query("agent_01", start, end)
        """
        ...

    def aggregate(self, agent_id: str, period: str) -> Awaitable["UsageAggregate"]:
        """Aggregate usage for an agent over a period.

        Args:
            agent_id: Agent identifier.
            period: Aggregation period (e.g. "hour", "day").

        Returns:
            Usage aggregate for the period.

        Example:
            >>> store = InMemoryMeteringStore()
            >>> agg = store.aggregate("agent_01", "day")
        """
        ...


class InMemoryMeteringStore:
    """In-memory implementation of :class:`AsyncMeteringStore` / :class:`MeteringStore`.

    Stores usage events in a list with asyncio.Lock for async-safe concurrent access.
    Used for testing and development; not persistent across restarts.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._events: list[UsageEvent] = []

    async def record(self, event: UsageEvent) -> None:
        """Append a usage event to the store (async-safe)."""
        async with self._lock:
            self._events.append(event)

    async def query(
        self,
        agent_id: str,
        start: datetime,
        end: datetime,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[UsageEvent]:
        """Return events for the agent in [start, end], sorted by timestamp."""
        if offset < 0:
            raise ValueError("offset must be non-negative")
        async with self._lock:
            out = [
                e for e in self._events if e.agent_id == agent_id and start <= e.timestamp <= end
            ]
        out = sorted(out, key=lambda e: e.timestamp)
        return out[offset : offset + limit] if limit is not None else out[offset:]

    async def aggregate(self, agent_id: str, period: str) -> UsageAggregate:
        """Aggregate all stored events for the agent into one UsageAggregate."""
        async with self._lock:
            agent_events = [e for e in self._events if e.agent_id == agent_id]
        total_tokens = sum(e.metrics.tokens_in + e.metrics.tokens_out for e in agent_events)
        total_duration = sum(e.metrics.duration_ms for e in agent_events)
        total_tasks = len(agent_events)
        total_api_calls = sum(e.metrics.api_calls for e in agent_events)
        return UsageAggregate(
            agent_id=agent_id,
            period=period,
            total_tokens=total_tokens,
            total_duration=total_duration,
            total_tasks=total_tasks,
            total_api_calls=total_api_calls,
        )


class MeteringStorageBridge:
    """Adapt a ``MeteringStorage`` (economics, flat ``UsageMetrics``) to ``MeteringStore``.

    The economics layer's ``MeteringStorage`` records flat ``UsageMetrics`` rows
    (task_id + agent_id + consumer_id + metrics + timestamp in one model) and
    exposes a richer query/aggregate/summary/stats/purge API. The state layer's
    ``MeteringStore`` — consumed by ``handlers.record_task_usage`` — records
    ``UsageEvent`` (identity + a ``UsageMetrics`` payload + timestamp) and
    exposes a minimal record/query/aggregate.

    This bridge lets a single ``MeteringStorage`` serve both the Usage REST API
    (which wants the rich interface) and the handler recording path (which wants
    ``MeteringStore``), without economics depending on state for the adapter.
    It is the only economics -> state bridge; v2.6.0 removed the old
    economics-layer wrapper function.

    Example:
        >>> from asap.economics.storage import InMemoryMeteringStorage
        >>> bridge = MeteringStorageBridge(InMemoryMeteringStorage())
        >>> hasattr(bridge, "record") and hasattr(bridge, "query")
        True
    """

    def __init__(self, storage: "MeteringStorage") -> None:
        self._storage = storage

    async def record(self, event: UsageEvent) -> None:
        """Record a ``UsageEvent`` by flattening it into a ``UsageMetrics`` row."""
        from asap.economics.metering import UsageMetrics

        await self._storage.record(UsageMetrics.from_usage_event(event))

    async def query(
        self,
        agent_id: str,
        start: datetime,
        end: datetime,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[UsageEvent]:
        """Return ``UsageEvent`` rows for ``agent_id`` in ``[start, end]``."""
        from asap.economics.storage import MeteringQuery

        filters = MeteringQuery(
            agent_id=agent_id,
            start=start,
            end=end,
            limit=limit,
            offset=offset,
        )
        events = await self._storage.query(filters)
        return [m.to_usage_event() for m in events]

    async def aggregate(self, agent_id: str, period: str) -> UsageAggregate:
        """Aggregate usage for ``agent_id`` over ``period`` into a state ``UsageAggregate``."""
        from asap.economics.metering import UsageAggregateByAgent

        filters = _period_to_metering_query(agent_id, period)
        aggs = await self._storage.aggregate("agent", filters=filters)
        for agg in aggs:
            if isinstance(agg, UsageAggregateByAgent) and agg.agent_id == agent_id:
                return UsageAggregate(
                    agent_id=agg.agent_id,
                    period=period,
                    total_tokens=agg.total_tokens,
                    total_duration=agg.total_duration_ms,
                    total_tasks=agg.total_tasks,
                    total_api_calls=agg.total_api_calls,
                )
        return UsageAggregate(agent_id=agent_id, period=period)


def _period_to_metering_query(agent_id: str, period: str) -> "MeteringQuery | None":
    """Convert a period string (hour/day/week/today) to a ``MeteringQuery`` time window.

    Returns ``None`` for unknown periods so the caller applies agent_id-only
    filtering (no time range). Mirrors the helper that previously lived in
    ``economics.storage._period_to_metering_query``; moved here so the bridge is
    self-contained and economics no longer needs to own the conversion.
    """
    from asap.economics.storage import MeteringQuery

    now = datetime.now(timezone.utc)
    if period in ("hour", "h"):
        start = now - timedelta(hours=1)
        return MeteringQuery(agent_id=agent_id, start=start, end=now)
    if period in ("day", "d", "today"):
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return MeteringQuery(agent_id=agent_id, start=start, end=now)
    if period in ("week", "w"):
        start = now - timedelta(weeks=1)
        return MeteringQuery(agent_id=agent_id, start=start, end=now)
    return MeteringQuery(agent_id=agent_id)


__all__ = [
    "AsyncMeteringStore",
    "InMemoryMeteringStore",
    "MeteringStorageBridge",
    "MeteringStore",
    "UsageAggregate",
    "UsageEvent",
    "UsageMetrics",
]
