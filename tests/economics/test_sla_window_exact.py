"""SLA rolling windows accept only ``1h``, ``24h``, ``7d``, and ``30d``.

``GET /sla`` and ``rolling_window_bounds`` already reject ``invalid`` and
``2d``. Neither token contains an allow-list window, and neither is contained
in one, so a containment or casefold check still rejects them. These
neighbors are the ones that check would admit.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from asap.economics import InMemorySLAStorage
from asap.economics.sla import rolling_window_bounds
from asap.models.entities import Manifest
from asap.transport.server import create_app

_END = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
_EXACT: dict[str, timedelta] = {
    "1h": timedelta(hours=1),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}
# Neighbors a containment, casefold, or strip comparison would treat as valid.
_NEIGHBORS = (
    "1H",
    " 1h",
    "1h ",
    "1hour",
    "1",
    "h",
    "24",
    "24hx",
    "24H",
    "7",
    "7dx",
    "7D",
    "d",
    "30",
    "30dx",
    "30D",
)
_HTTP_DETAIL = "window must be one of: 1h, 24h, 7d, 30d"


@pytest.fixture
def sla_storage() -> InMemorySLAStorage:
    """Fresh in-memory SLA store for the HTTP route."""
    return InMemorySLAStorage()


def _client(manifest: Manifest, storage: InMemorySLAStorage) -> TestClient:
    """SLA API client with its own high rate-limit budget."""
    app = create_app(
        manifest,
        sla_storage=storage,
        rate_limit="999999/minute",
    )
    return TestClient(app)


def test_sla_window_query_is_exact(
    sample_manifest: Manifest,
    sla_storage: InMemorySLAStorage,
) -> None:
    """Allow-list windows echo back. Neighbors are HTTP 400."""
    client = _client(sample_manifest, sla_storage)
    for window in _EXACT:
        response = client.get("/sla", params={"window": window})
        assert response.status_code == 200, window
        assert response.json()["window"] == window
    for window in _NEIGHBORS:
        response = client.get("/sla", params={"window": window})
        assert response.status_code == 400, window
        assert response.json()["detail"] == _HTTP_DETAIL


def test_rolling_window_bounds_is_exact() -> None:
    """Each allow-list window has its delta. Neighbors raise ValueError."""
    for window, delta in _EXACT.items():
        start, end = rolling_window_bounds(window, _END)
        assert end == _END
        assert end - start == delta
    for window in _NEIGHBORS:
        with pytest.raises(ValueError, match="window must be one of") as exc_info:
            rolling_window_bounds(window, _END)
        assert repr(window) in str(exc_info.value)
