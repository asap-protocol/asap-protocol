"""Fetch behavior of the guarded fetch helper: pinning, limits, redirects.

No test touches the network: the resolver and the httpx transport are fakes.
"""

from __future__ import annotations

import asyncio
import gzip
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest

from asap.utils.guarded_fetch import AddressResolver, GuardedFetchError, guarded_fetch_json

_PUBLIC_ADDRESS = "93.184.216.34"
_JWKS_URL = "https://jwks.example.com/keys"


async def _public_resolver(hostname: str) -> list[str]:
    return [_PUBLIC_ADDRESS]


class _DripByteStream(httpx.AsyncByteStream):
    """Response body that waits before its last byte, like a slow-drip server."""

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b'{"keys": '
        await asyncio.sleep(5)
        yield b"[]}"


class _EndlessByteStream(httpx.AsyncByteStream):
    """Response body that never ends; records how many chunks were pulled."""

    def __init__(self) -> None:
        self.chunks_sent = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        while True:
            self.chunks_sent += 1
            yield b"x" * 512


async def _fetch(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    timeout_seconds: float = 5.0,
    resolver: AddressResolver = _public_resolver,
) -> dict[str, Any]:
    return await guarded_fetch_json(
        _JWKS_URL,
        max_bytes=1024,
        timeout_seconds=timeout_seconds,
        resolver=resolver,
        transport=httpx.MockTransport(handler),
    )


async def test_connection_is_pinned_to_the_checked_address() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"keys": []})

    assert await _fetch(handler) == {"keys": []}
    assert seen[0].url.host == _PUBLIC_ADDRESS
    assert seen[0].headers["Host"] == "jwks.example.com"
    assert seen[0].extensions["sni_hostname"] == "jwks.example.com"
    assert seen[0].headers["Accept-Encoding"] == "identity"


async def test_endless_response_is_refused_while_reading() -> None:
    stream = _EndlessByteStream()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream)

    with pytest.raises(GuardedFetchError) as caught:
        await _fetch(handler)
    assert caught.value.code == "too_large"
    assert stream.chunks_sent == 3


async def test_slow_response_is_refused_by_the_total_deadline() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=_DripByteStream())

    with pytest.raises(GuardedFetchError) as caught:
        await _fetch(handler, timeout_seconds=0.05)
    assert caught.value.code == "timeout"


@pytest.mark.parametrize(
    ("status", "body", "headers", "code"),
    [
        (302, b"", {"Location": "https://169.254.169.254/"}, "redirect_refused"),
        (200, b"{}", {"Content-Length": "4096"}, "too_large"),
        (500, b'{"error": "boom"}', {}, "http_status"),
        (200, b"[1, 2]", {}, "invalid_json"),
        (200, b'{"exp": NaN}', {}, "invalid_json"),
        (200, gzip.compress(b"{}", mtime=0), {"Content-Encoding": "gzip"}, "content_encoding"),
    ],
)
async def test_bad_responses_are_refused(
    status: int, body: bytes, headers: dict[str, str], code: str
) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(status, stream=httpx.ByteStream(body), headers=headers)

    with pytest.raises(GuardedFetchError) as caught:
        await _fetch(handler)
    assert caught.value.code == code
    assert len(calls) == 1, "a redirect or a retry must never cause a second request"


async def test_too_deeply_nested_json_is_invalid_json() -> None:
    # json.loads raises RecursionError, not ValueError, past about 10,000 levels.
    body = b'{"keys": ' + b"[" * 100_000 + b"]" * 100_000 + b"}"

    with pytest.raises(GuardedFetchError) as caught:
        await guarded_fetch_json(
            _JWKS_URL,
            resolver=_public_resolver,
            transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body)),
        )
    assert caught.value.code == "invalid_json"


async def test_transport_error_is_reported_without_the_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(GuardedFetchError) as caught:
        await _fetch(handler)
    assert caught.value.code == "transport_error"


async def test_private_host_is_refused_before_any_request() -> None:
    calls: list[str] = []

    async def private_resolver(hostname: str) -> list[str]:
        return ["10.0.0.7"]

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={})

    with pytest.raises(GuardedFetchError) as caught:
        await _fetch(handler, resolver=private_resolver)
    assert caught.value.code == "host_not_public"
    assert calls == []


async def test_switch_allows_a_loopback_fetch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "127.0.0.1"
        return httpx.Response(200, json={"keys": []})

    body = await guarded_fetch_json(
        "https://127.0.0.1:8443/keys",
        allow_private_hosts=True,
        transport=httpx.MockTransport(handler),
    )
    assert body == {"keys": []}
