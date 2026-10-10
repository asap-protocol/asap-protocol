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

    def fake_test(rate_limit: Any, key: str, cost: int = 1) -> bool:
        _ = cost
        tested.append(f"{rate_limit}:{key}")
        return len(tested) == 1

    def fake_hit(rate_limit: Any, key: str, cost: int = 1) -> bool:
        _ = cost
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


def test_check_n_records_one_hit_per_accepted_item() -> None:
    """A successful batch spends one hit per item, so a later call sees the full count."""
    limiter = ASAPRateLimiter(
        key_func=lambda _request: "batch-client",
        limits=["5/minute"],
    )
    limiter.check_n(_request(), 3)
    limiter.check_n(_request(), 2)
    with pytest.raises(RateLimitExceeded):
        limiter.check(_request())


def test_check_n_rejects_batch_larger_than_remaining_quota_without_consuming() -> None:
    """A batch that does not fit must raise without debiting partial hits."""
    limiter = ASAPRateLimiter(
        key_func=lambda _request: "batch-client",
        limits=["5/minute"],
    )
    limiter.check_n(_request(), 4)
    with pytest.raises(RateLimitExceeded):
        limiter.check_n(_request(), 2)
    limiter.check(_request())


def test_check_n_rejects_oversized_batch_on_empty_window() -> None:
    """``test(cost=n)`` must fail before any ``hit`` when n exceeds the window."""
    limiter = ASAPRateLimiter(
        key_func=lambda _request: "batch-client",
        limits=["5/minute"],
    )
    with pytest.raises(RateLimitExceeded):
        limiter.check_n(_request(), 6)
    limiter.check_n(_request(), 5)
    with pytest.raises(RateLimitExceeded):
        limiter.check(_request())


def test_check_n_non_positive_does_not_consult_the_window() -> None:
    """Zero and negative batch sizes return before any limiter key or counter is touched."""

    def _reject_lookup(_request: Request) -> str:
        raise AssertionError("key_func must not run for a non-positive batch")

    limiter = ASAPRateLimiter(key_func=_reject_lookup, limits=["1/minute"])
    limiter.check_n(_request(), 0)
    limiter.check_n(_request(), -4)
