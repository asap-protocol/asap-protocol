"""Lock the opaque-token introspection cache expiry buffer.

A token with ``exp`` inside ``EXPIRY_BUFFER_SECONDS`` must not be reused from
cache. One second past that buffer is cached, and the active TTL stays capped.
"""

from __future__ import annotations

import httpx
import pytest

from asap.auth.introspection import (
    EXPIRY_BUFFER_SECONDS,
    MAX_ACTIVE_CACHE_TTL_SECONDS,
    TokenInfo,
    TokenIntrospector,
)

_FROZEN_NOW = 1_700_000_000.0


def _freeze_introspection_clock(monkeypatch: pytest.MonkeyPatch, now: float) -> None:
    """Pin ``time.time`` for TTL math and cache entry expiry."""
    monkeypatch.setattr("asap.auth.introspection.time.time", lambda: now)


def test_cache_ttl_is_zero_through_expiry_buffer(monkeypatch: pytest.MonkeyPatch) -> None:
    """TTL is 0 at the buffer instant and 1 one second later."""
    _freeze_introspection_clock(monkeypatch, _FROZEN_NOW)
    buffer = int(EXPIRY_BUFFER_SECONDS)
    inside = TokenInfo(active=True, sub="agent", exp=int(_FROZEN_NOW) + buffer - 1)
    on_buffer = TokenInfo(active=True, sub="agent", exp=int(_FROZEN_NOW) + buffer)
    just_outside = TokenInfo(active=True, sub="agent", exp=int(_FROZEN_NOW) + buffer + 1)

    assert inside.cache_ttl_seconds() == 0.0
    assert on_buffer.cache_ttl_seconds() == 0.0
    assert just_outside.cache_ttl_seconds() == 1.0


def test_cache_ttl_caps_at_max_on_the_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remaining lifetime equal to the cap is kept; one extra second is still capped."""
    _freeze_introspection_clock(monkeypatch, _FROZEN_NOW)
    cap = int(MAX_ACTIVE_CACHE_TTL_SECONDS)
    buffer = int(EXPIRY_BUFFER_SECONDS)
    at_cap = TokenInfo(active=True, sub="agent", exp=int(_FROZEN_NOW) + buffer + cap)
    over_cap = TokenInfo(active=True, sub="agent", exp=int(_FROZEN_NOW) + buffer + cap + 1)

    assert at_cap.cache_ttl_seconds() == MAX_ACTIVE_CACHE_TTL_SECONDS
    assert over_cap.cache_ttl_seconds() == MAX_ACTIVE_CACHE_TTL_SECONDS


def _introspector(handler: httpx.MockTransport) -> TokenIntrospector:
    return TokenIntrospector(
        introspection_url="https://auth.example.com/oauth/introspect",
        client_id="client",
        client_secret="secret",
        transport=handler,
    )


async def test_introspect_refetches_token_at_expiry_buffer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A token with exactly the buffer of life left is not served on the next call."""
    _freeze_introspection_clock(monkeypatch, _FROZEN_NOW)
    calls = {"count": 0}
    exp = int(_FROZEN_NOW) + int(EXPIRY_BUFFER_SECONDS)

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(
            status_code=200,
            json={
                "active": True,
                "sub": f"sub-{calls['count']}",
                "scope": "asap:read",
                "exp": exp,
            },
        )

    introspector = _introspector(httpx.MockTransport(handler))
    first = await introspector.introspect("opaque-near-expiry")
    second = await introspector.introspect("opaque-near-expiry")

    assert first is not None and first.sub == "sub-1"
    assert second is not None and second.sub == "sub-2"
    assert calls["count"] == 2


async def test_introspect_reuses_token_one_second_past_expiry_buffer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One second past the buffer, the same opaque token is served from cache."""
    _freeze_introspection_clock(monkeypatch, _FROZEN_NOW)
    calls = {"count": 0}
    exp = int(_FROZEN_NOW) + int(EXPIRY_BUFFER_SECONDS) + 1

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(
            status_code=200,
            json={
                "active": True,
                "sub": "cached-subject",
                "scope": "asap:read",
                "exp": exp,
            },
        )

    introspector = _introspector(httpx.MockTransport(handler))
    first = await introspector.introspect("opaque-fresh")
    second = await introspector.introspect("opaque-fresh")

    assert first is not None and first.sub == "cached-subject"
    assert second is not None and second.sub == "cached-subject"
    assert calls["count"] == 1
