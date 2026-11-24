"""``TokenIntrospector(max_cache_ttl_seconds=...)``: a caller's cap on the answer cache.

The external-token path passes each issuer's ``introspection.cache_ttl_seconds``
so a revoked opaque token stops working within that time. Without the keyword
the cache keeps its lifetime-derived TTL.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import httpx
import pytest

from asap.auth.introspection import TokenInfo, TokenIntrospector

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


async def test_capped_cache_serializes_misses_only_for_the_same_token() -> None:
    first_started = asyncio.Event()
    allow_first_answer = asyncio.Event()
    second_entered = asyncio.Event()
    calls: list[str] = []

    async def post(
        url: str, form: Mapping[str, str], *, basic_auth: tuple[str, str]
    ) -> dict[str, Any]:
        token = form["token"]
        calls.append(token)
        if token == "same":
            first_started.set()
            await allow_first_answer.wait()
        return {"active": False}

    async def check_again() -> None:
        second_entered.set()
        assert await introspector.introspect("same") is None

    introspector = TokenIntrospector(
        "https://auth.example/introspect",
        "client",
        "secret",
        poster=post,
        max_cache_ttl_seconds=10,
    )
    async with asyncio.TaskGroup() as tasks:
        first = tasks.create_task(introspector.introspect("same"))
        await first_started.wait()
        tasks.create_task(check_again())
        try:
            await second_entered.wait()
            assert await asyncio.wait_for(introspector.introspect("other"), timeout=2) is None
            assert calls == ["same", "other"]
        finally:
            allow_first_answer.set()

    assert first.result() is None
    assert calls == ["same", "other"]


async def test_capped_refresh_notifies_a_caller_on_another_event_loop() -> None:
    main_loop = asyncio.get_running_loop()
    first_started = asyncio.Event()
    allow_first_answer = asyncio.Event()
    second_waiting = asyncio.Event()
    calls = 0

    async def post(
        url: str, form: Mapping[str, str], *, basic_auth: tuple[str, str]
    ) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        first_started.set()
        await allow_first_answer.wait()
        return {"active": False}

    async def check_in_other_loop() -> TokenInfo | None:
        request = asyncio.create_task(introspector.introspect("same"))
        asyncio.get_running_loop().call_soon(main_loop.call_soon_threadsafe, second_waiting.set)
        return await asyncio.wait_for(request, timeout=3)

    introspector = TokenIntrospector(
        "https://auth.example/introspect",
        "client",
        "secret",
        poster=post,
        max_cache_ttl_seconds=10,
    )
    async with asyncio.TaskGroup() as tasks:
        first = tasks.create_task(introspector.introspect("same"))
        await asyncio.wait_for(first_started.wait(), timeout=2)
        second = tasks.create_task(
            asyncio.to_thread(asyncio.run, check_in_other_loop(), debug=True)
        )
        try:
            await asyncio.wait_for(second_waiting.wait(), timeout=2)
        finally:
            allow_first_answer.set()

    assert first.result() is second.result() is None
    assert calls == 1


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
