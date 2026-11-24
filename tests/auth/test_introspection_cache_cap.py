"""``TokenIntrospector(max_cache_ttl_seconds=...)``: a caller's cap on the answer cache.

The external-token path passes each issuer's ``introspection.cache_ttl_seconds``
so a revoked opaque token stops working within that time. Without the keyword
the cache keeps its lifetime-derived TTL.
"""

from __future__ import annotations

import httpx
import pytest

from asap.auth.introspection import TokenIntrospector

_NOW = 1_700_000_000.0


class _Endpoint:
    """Answers with ``active``; counts the requests."""

    def __init__(self) -> None:
        self.active = True
        self.calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        return httpx.Response(200, json={"active": self.active, "sub": "s", "exp": _NOW + 3600})


def _introspector(endpoint: _Endpoint, cap: float | None) -> TokenIntrospector:
    return TokenIntrospector(
        "https://auth.example/introspect",
        "client",
        "secret",
        transport=httpx.MockTransport(endpoint),
        max_cache_ttl_seconds=cap,
    )


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> dict[str, float]:
    now = {"value": _NOW}
    monkeypatch.setattr("asap.auth.introspection.time.time", lambda: now["value"])
    return now


async def test_cap_shortens_the_active_answer_ttl(clock: dict[str, float]) -> None:
    endpoint = _Endpoint()
    introspector = _introspector(endpoint, 10)
    assert await introspector.introspect("t") is not None
    clock["value"] += 9
    assert await introspector.introspect("t") is not None
    assert endpoint.calls == 1

    endpoint.active = False
    clock["value"] += 1
    assert await introspector.introspect("t") is None
    assert endpoint.calls == 2


async def test_cap_of_zero_asks_the_endpoint_every_time(clock: dict[str, float]) -> None:
    endpoint = _Endpoint()
    introspector = _introspector(endpoint, 0)
    for _ in range(3):
        await introspector.introspect("t")
    assert endpoint.calls == 3


async def test_without_a_cap_the_lifetime_ttl_applies(clock: dict[str, float]) -> None:
    endpoint = _Endpoint()
    introspector = _introspector(endpoint, None)
    await introspector.introspect("t")
    clock["value"] += 600
    await introspector.introspect("t")
    assert endpoint.calls == 1


@pytest.mark.parametrize("cap", [-1.0, float("nan")])
def test_negative_or_nan_cap_is_refused(cap: float) -> None:
    with pytest.raises(ValueError, match="max_cache_ttl_seconds"):
        _introspector(_Endpoint(), cap)
