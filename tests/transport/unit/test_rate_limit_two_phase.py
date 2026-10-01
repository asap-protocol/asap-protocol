"""Two-phase HTTP rate limiting must not consume hits on a partial failure."""

from __future__ import annotations

import time
from typing import Any

import pytest
from fastapi import Request

from asap.transport.rate_limit import ASAPRateLimiter, RateLimitExceeded


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": "/asap",
            "raw_path": b"/asap",
            "query_string": b"",
            "headers": [],
            "client": ("203.0.113.10", 443),
            "server": ("asap.test", 443),
        }
    )


def _limiter_with_spies() -> tuple[ASAPRateLimiter, list[str], list[str]]:
    limiter = ASAPRateLimiter(
        key_func=lambda _request: "client-key",
        limits=["10/second;100/minute"],
    )
    tested: list[str] = []
    hits: list[str] = []

    def fake_test(rate_limit: Any, key: str) -> bool:
        tested.append(f"{rate_limit}:{key}")
        return len(tested) == 1

    def fake_hit(rate_limit: Any, key: str) -> bool:
        hits.append(f"{rate_limit}:{key}")
        return True

    def fake_stats(rate_limit: Any, key: str) -> tuple[float, int]:
        _ = (rate_limit, key)
        return (time.time() + 30, 0)

    limiter._strategy.test = fake_test
    limiter._strategy.hit = fake_hit
    limiter._strategy.get_window_stats = fake_stats
    return limiter, tested, hits


def test_check_does_not_consume_hits_when_a_later_limit_fails() -> None:
    """A failing window must not increment counters that already passed."""
    limiter, tested, hits = _limiter_with_spies()
    with pytest.raises(RateLimitExceeded) as exc_info:
        limiter.check(_request())
    assert len(tested) == 2
    assert hits == []
    assert exc_info.value.retry_after >= 1


def test_check_n_does_not_consume_hits_when_a_limit_fails() -> None:
    """A batch pre-check fails closed without recording the batch size."""
    limiter, tested, hits = _limiter_with_spies()
    with pytest.raises(RateLimitExceeded):
        limiter.check_n(_request(), 5)
    assert len(tested) == 2
    assert hits == []
