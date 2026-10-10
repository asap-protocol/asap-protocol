"""Billing aggregates must ignore read pagination and use ISO week-years."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from asap.economics import (
    InMemoryMeteringStorage,
    MeteringQuery,
    SQLiteMeteringStorage,
    UsageMetrics,
)
from asap.economics.storage import MeteringStorage

_SUNDAY_2025_W52 = datetime(2025, 12, 28, 12, 0, tzinfo=timezone.utc)
_MONDAY_2026_W01 = datetime(2025, 12, 29, 12, 0, tzinfo=timezone.utc)
_THURSDAY_2026_W01 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def _usage(
    task_id: str,
    tokens_in: int,
    tokens_out: int,
    when: datetime,
    *,
    agent_id: str = "agent-bill",
) -> UsageMetrics:
    return UsageMetrics(
        task_id=task_id,
        agent_id=agent_id,
        consumer_id="consumer-bill",
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        timestamp=when,
    )


async def _assert_iso_week_year(storage: MeteringStorage) -> None:
    """Sunday 2025-12-28 is 2025-W52; the next two days share 2026-W01."""
    await storage.record(_usage("w52", 10, 10, _SUNDAY_2025_W52))
    await storage.record(_usage("w01-a", 5, 5, _MONDAY_2026_W01))
    await storage.record(_usage("w01-b", 1, 1, _THURSDAY_2026_W01))

    aggregates = await storage.aggregate("week")
    by_period = {row.period: row for row in aggregates}
    assert set(by_period) == {"2025-W52", "2026-W01"}
    assert by_period["2025-W52"].total_tokens == 20
    assert by_period["2025-W52"].total_tasks == 1
    assert by_period["2026-W01"].total_tokens == 12
    assert by_period["2026-W01"].total_tasks == 2


async def _assert_aggregate_ignores_page_window(storage: MeteringStorage) -> None:
    """limit/offset page the event list and must not shrink the bill."""
    await storage.record(_usage("page-1", 10, 10, datetime(2026, 3, 1, tzinfo=timezone.utc)))
    await storage.record(_usage("page-2", 20, 20, datetime(2026, 3, 2, tzinfo=timezone.utc)))
    await storage.record(_usage("page-3", 5, 5, datetime(2026, 3, 3, tzinfo=timezone.utc)))
    page = MeteringQuery(limit=1, offset=1)

    rows = await storage.query(page)
    assert [row.task_id for row in rows] == ["page-2"]

    aggregates = await storage.aggregate("agent", filters=page)
    assert len(aggregates) == 1
    assert aggregates[0].total_tokens == 70
    assert aggregates[0].total_tasks == 3

    summary = await storage.summary(filters=page)
    assert summary.total_tokens == 70
    assert summary.total_tasks == 3


async def test_in_memory_week_aggregate_uses_iso_week_year() -> None:
    await _assert_iso_week_year(InMemoryMeteringStorage())


async def test_sqlite_week_aggregate_uses_iso_week_year(tmp_path: Path) -> None:
    storage = SQLiteMeteringStorage(db_path=str(tmp_path / "iso-week.db"))
    await _assert_iso_week_year(storage)


async def test_in_memory_aggregate_ignores_query_pagination() -> None:
    await _assert_aggregate_ignores_page_window(InMemoryMeteringStorage())


async def test_sqlite_aggregate_ignores_query_pagination(tmp_path: Path) -> None:
    storage = SQLiteMeteringStorage(db_path=str(tmp_path / "page.db"))
    await _assert_aggregate_ignores_page_window(storage)
