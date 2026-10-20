"""WebSocket heartbeat eviction uses a strict greater-than idle fence.

``_heartbeat_loop`` closes a connection only when the silence is greater
than ``STALE_CONNECTION_TIMEOUT``. An inclusive comparison would drop a
client that last sent a frame at exactly that timeout.
"""

from __future__ import annotations

import asyncio
import json
import math
from unittest.mock import AsyncMock

import pytest
from fastapi import WebSocket

from asap.transport.ws.codecs import HEARTBEAT_FRAME_TYPE_PING, STALE_CONNECTION_TIMEOUT
from asap.transport.ws.server import _heartbeat_loop

_LAST_RECEIVED = 1_000_000.0


def _freeze_heartbeat_clock(monkeypatch: pytest.MonkeyPatch, now: float) -> None:
    """Skip the ping sleep and pin the clock used for the stale check."""

    async def _sleep(_interval: float) -> None:
        return None

    monkeypatch.setattr("asap.transport.ws.server.time.monotonic", lambda: now)
    monkeypatch.setattr("asap.transport.ws.server.asyncio.sleep", _sleep)


def _websocket() -> WebSocket:
    """Return a socket double whose ``send_text`` can be awaited."""
    return AsyncMock(spec=WebSocket)


@pytest.mark.asyncio
async def test_heartbeat_pings_when_idle_equals_stale_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Silence equal to the stale timeout still receives a ping."""
    _freeze_heartbeat_clock(monkeypatch, _LAST_RECEIVED + STALE_CONNECTION_TIMEOUT)
    closed = asyncio.Event()
    websocket = _websocket()
    sent: list[str] = []

    async def _send_text(payload: str) -> None:
        sent.append(payload)
        closed.set()

    websocket.send_text.side_effect = _send_text
    await _heartbeat_loop(websocket, [_LAST_RECEIVED], closed)

    websocket.send_text.assert_awaited_once()
    assert sent == [json.dumps({"type": HEARTBEAT_FRAME_TYPE_PING})]
    assert closed.is_set()


@pytest.mark.asyncio
async def test_heartbeat_closes_one_step_past_stale_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One float step past the stale timeout closes the socket without a ping."""
    equal_instant = _LAST_RECEIVED + STALE_CONNECTION_TIMEOUT
    now = math.nextafter(equal_instant, math.inf)
    if not now - _LAST_RECEIVED > STALE_CONNECTION_TIMEOUT:
        raise AssertionError(
            f"idle age {now - _LAST_RECEIVED!r} is not past {STALE_CONNECTION_TIMEOUT!r}"
        )
    _freeze_heartbeat_clock(monkeypatch, now)
    closed = asyncio.Event()
    websocket = _websocket()

    await _heartbeat_loop(websocket, [_LAST_RECEIVED], closed)

    assert closed.is_set()
    websocket.send_text.assert_not_awaited()
