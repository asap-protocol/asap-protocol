"""Tests for the bounded HTTP exchange that both compliance transports use."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from asap_compliance.validators.http_exchange import (
    HttpExchangeError,
    RawHttpAnswer,
    bounded_http_exchange,
)
from asap_compliance.validators.mcp_http_transport import McpHttpTransportError

_URL = "https://gateway.example/.well-known/agent-card.json"
_SECRET = "secret-probe-token"


async def _exchange(
    transport: httpx.MockTransport,
    *,
    timeout_seconds: float = 5.0,
    error_type: type[HttpExchangeError] = HttpExchangeError,
) -> RawHttpAnswer:
    return await bounded_http_exchange(
        "GET",
        _URL,
        headers={"Authorization": f"Bearer {_SECRET}"},
        timeout_seconds=timeout_seconds,
        max_bytes=16,
        http_transport=transport,
        where=f"GET {_URL}",
        error_type=error_type,
    )


class TestBoundedExchange:
    async def test_a_redirect_comes_back_as_it_is_with_status_headers_and_bytes(self) -> None:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(302, content=b"moved", headers={"location": "/x"})
        )

        answer = await _exchange(transport)

        assert (answer.status_code, answer.headers["location"]) == (302, "/x")
        assert answer.body == b"moved"

    async def test_a_body_over_the_cap_raises_the_given_error_type(self) -> None:
        transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 17))

        with pytest.raises(McpHttpTransportError, match="over 16 bytes"):
            await _exchange(transport, error_type=McpHttpTransportError)

    async def test_a_slow_answer_raises_after_the_one_deadline(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(5)
            return httpx.Response(200)

        with pytest.raises(HttpExchangeError, match="within 0.05s"):
            await _exchange(httpx.MockTransport(handler), timeout_seconds=0.05)

    async def test_a_network_error_names_its_type_and_never_a_header(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError(f"refused with {request.headers}", request=request)

        with pytest.raises(HttpExchangeError, match="ConnectError") as excinfo:
            await _exchange(httpx.MockTransport(handler))

        assert _SECRET not in str(excinfo.value)
        assert excinfo.value.__cause__ is None
