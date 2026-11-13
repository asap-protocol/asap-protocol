"""Usage aggregate ``group_by`` accepts only the four allow-list tokens.

``GET /usage/aggregate`` and both metering stores already reject ``invalid``.
That token is not a prefix, suffix, case variant, or padded copy of
``agent``, ``consumer``, ``day``, or ``week``, so a containment or casefold
check still passes it. These neighbors are the ones that check would admit.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from asap.economics.metering import UsageMetrics
from asap.economics.storage import InMemoryMeteringStorage, SQLiteMeteringStorage
from asap.models.entities import Manifest
from asap.transport.server import create_app

_STAMP = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
_EXACT = ("agent", "consumer", "day", "week")
# Neighbors a containment, casefold, or strip comparison would treat as valid.
_NEIGHBORS = (
    "agents",
    "age",
    "Agent",
    " agent",
    "agent ",
    "consumers",
    "cons",
    "Consumer",
    "daylight",
    "da",
    "Day",
    "weeks",
    "wee",
    "Week",
)
_HTTP_DETAIL = "group_by must be one of: agent, consumer, day, week"


def _open_store(
    backend: str,
    tmp_path: Path,
) -> InMemoryMeteringStorage | SQLiteMeteringStorage:
    """Return an empty usage store for ``backend``."""
    if backend == "memory":
        return InMemoryMeteringStorage()
    if backend == "sqlite":
        return SQLiteMeteringStorage(db_path=tmp_path / "group-by-exact.db")
    raise ValueError(f"backend must be 'memory' or 'sqlite', got {backend!r}")


def _metrics() -> UsageMetrics:
    """One usage row so an accepted group_by returns a bucket."""
    return UsageMetrics(
        task_id="task_group_01",
        agent_id="urn:asap:agent:usage-group-01",
        consumer_id="urn:asap:agent:usage-caller-01",
        tokens_in=2,
        tokens_out=3,
        timestamp=_STAMP,
    )


@pytest.fixture
def metering_storage() -> InMemoryMeteringStorage:
    """Fresh in-memory store for the usage HTTP route."""
    return InMemoryMeteringStorage()


def _client(manifest: Manifest, storage: InMemoryMeteringStorage) -> TestClient:
    """Usage API client with its own high rate-limit budget."""
    app = create_app(
        manifest,
        metering_storage=storage,
        rate_limit="999999/minute",
    )
    return TestClient(app)


def test_usage_aggregate_group_by_is_exact(
    sample_manifest: Manifest,
    metering_storage: InMemoryMeteringStorage,
) -> None:
    """Allow-list tokens group an empty store. Neighbors are HTTP 400."""
    client = _client(sample_manifest, metering_storage)
    for group_by in _EXACT:
        response = client.get("/usage/aggregate", params={"group_by": group_by})
        assert response.status_code == 200, group_by
        body = response.json()
        assert body["group_by"] == group_by
        assert body["data"] == []
    for group_by in _NEIGHBORS:
        response = client.get("/usage/aggregate", params={"group_by": group_by})
        assert response.status_code == 400, group_by
        assert response.json()["detail"] == _HTTP_DETAIL


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["memory", "sqlite"])
async def test_storage_aggregate_group_by_is_exact(backend: str, tmp_path: Path) -> None:
    """Each store groups an exact token and raises ValueError for a neighbor."""
    store = _open_store(backend, tmp_path)
    await store.record(_metrics())
    for group_by in _EXACT:
        grouped = await store.aggregate(group_by)
        assert len(grouped) == 1, group_by
    for group_by in _NEIGHBORS:
        with pytest.raises(ValueError, match="group_by must be one of") as exc_info:
            await store.aggregate(group_by)
        assert group_by in str(exc_info.value)
