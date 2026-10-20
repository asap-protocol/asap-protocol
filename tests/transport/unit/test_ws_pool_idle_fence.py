"""Pooled WebSocket sockets stay usable through the exact idle timeout.

``WebSocketConnectionPool.acquire`` closes a queued socket only when
``now - last_used`` is greater than ``idle_timeout``. The same comparison
runs on the fast path and again after the pool is full and ``acquire``
waits. An inclusive comparison would drop a socket that has been idle for
exactly the configured timeout.
"""

from __future__ import annotations

import math
from typing import cast
from unittest.mock import AsyncMock

import pytest
from websockets.legacy.client import WebSocketClientProtocol

from asap.transport.ws.client import WebSocketTransport
from asap.transport.ws.pool import WebSocketConnectionPool

_NOW = 1_000.0
_IDLE_TIMEOUT = 60.0
_POOL_URL = "ws://localhost:8080/asap/ws"


def _freeze_monotonic(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the pool clock so the idle age is exact."""
    monkeypatch.setattr("asap.transport.ws.pool.time.monotonic", lambda: _NOW)


def _queued_transport() -> WebSocketTransport:
    """Build a connected socket double that records ``close``."""
    transport = WebSocketTransport()
    transport._ws = cast(WebSocketClientProtocol, object())
    transport.close = AsyncMock()
    return transport


def _age_one_step_past_timeout() -> float:
    """Return an idle age that is the next float above the timeout."""
    last_used = math.nextafter(_NOW - _IDLE_TIMEOUT, -math.inf)
    age = _NOW - last_used
    if not age > _IDLE_TIMEOUT:
        raise AssertionError(
            f"idle age {age!r} is not greater than timeout {_IDLE_TIMEOUT!r}; "
            "the one-step fixture collapsed under float rounding"
        )
    return age


async def _forbid_connect(transport: WebSocketTransport, url: str) -> None:
    """Fail the test if acquire opens a socket that should have been reused."""
    raise AssertionError(f"connect should not run for a pooled socket, got {url} on {transport}")


@pytest.mark.asyncio
async def test_fast_path_reuses_socket_at_exact_idle_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A queued socket idle for exactly ``idle_timeout`` is reused."""
    _freeze_monotonic(monkeypatch)
    pool = WebSocketConnectionPool(_POOL_URL, max_size=2, idle_timeout=_IDLE_TIMEOUT)
    fresh = _queued_transport()
    pool._available.put_nowait((fresh, _NOW - _IDLE_TIMEOUT))
    pool._total_count = 1

    monkeypatch.setattr(WebSocketTransport, "connect", _forbid_connect)
    acquired = await pool.acquire()

    assert acquired is fresh
    fresh.close.assert_not_awaited()
    assert pool._in_use_count == 1
    assert pool._total_count == 1


@pytest.mark.asyncio
async def test_fast_path_closes_socket_one_step_past_idle_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A queued socket one float step past ``idle_timeout`` is closed."""
    _freeze_monotonic(monkeypatch)
    pool = WebSocketConnectionPool(_POOL_URL, max_size=2, idle_timeout=_IDLE_TIMEOUT)
    stale = _queued_transport()
    pool._available.put_nowait((stale, _NOW - _age_one_step_past_timeout()))
    pool._total_count = 1
    created: list[WebSocketTransport] = []

    async def _connect(transport: WebSocketTransport, url: str) -> None:
        transport._ws = cast(WebSocketClientProtocol, object())
        created.append(transport)
        assert url == _POOL_URL

    monkeypatch.setattr(WebSocketTransport, "connect", _connect)
    acquired = await pool.acquire()

    stale.close.assert_awaited_once()
    assert acquired is not stale
    assert created == [acquired]
    assert pool._in_use_count == 1
    assert pool._total_count == 1


@pytest.mark.asyncio
async def test_waiting_path_reuses_socket_at_exact_idle_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The saturated-pool wait uses the same exclusive idle fence."""
    _freeze_monotonic(monkeypatch)
    pool = WebSocketConnectionPool(_POOL_URL, max_size=1, idle_timeout=_IDLE_TIMEOUT)
    fresh = _queued_transport()
    pool._total_count = 1
    waits = 0

    async def _queued_get() -> tuple[WebSocketTransport, float]:
        nonlocal waits
        waits += 1
        if waits > 1:
            raise AssertionError("acquire waited again after the exact-timeout socket")
        return fresh, _NOW - _IDLE_TIMEOUT

    pool._available.get = _queued_get
    monkeypatch.setattr(WebSocketTransport, "connect", _forbid_connect)
    acquired = await pool.acquire()

    assert acquired is fresh
    fresh.close.assert_not_awaited()
    assert pool._in_use_count == 1
    assert pool._total_count == 1


@pytest.mark.asyncio
async def test_waiting_path_closes_socket_one_step_past_idle_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A socket returned to a full pool is closed once it is past the timeout."""
    _freeze_monotonic(monkeypatch)
    pool = WebSocketConnectionPool(_POOL_URL, max_size=1, idle_timeout=_IDLE_TIMEOUT)
    stale = _queued_transport()
    fresh = _queued_transport()
    queued = [
        (stale, _NOW - _age_one_step_past_timeout()),
        (fresh, _NOW),
    ]
    pool._total_count = 2

    async def _queued_get() -> tuple[WebSocketTransport, float]:
        return queued.pop(0)

    pool._available.get = _queued_get
    monkeypatch.setattr(WebSocketTransport, "connect", _forbid_connect)
    acquired = await pool.acquire()

    assert acquired is fresh
    stale.close.assert_awaited_once()
    fresh.close.assert_not_awaited()
    assert pool._in_use_count == 1
    assert pool._total_count == 1
