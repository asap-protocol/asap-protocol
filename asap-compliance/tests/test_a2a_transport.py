"""Tests for the plain HTTP A2A transport of the ``a2a-bridge`` profile."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from asap_compliance.validators.a2a_transport import (
    MAX_RESPONSE_BYTES,
    A2AHttpTransport,
    A2AHttpTransportError,
)
from asap_compliance.validators.http_exchange import HttpExchangeError
from asap_compliance.validators.mcp_http_transport import McpHttpTransportError

_BASE_URL = "https://gateway.example"
_CARD = {"name": "A2A Gateway Demo", "version": "1.0.0"}


def _transport(handler: Any, *, timeout_seconds: float = 5.0) -> A2AHttpTransport:
    return A2AHttpTransport(
        _BASE_URL, timeout_seconds=timeout_seconds, http_transport=httpx.MockTransport(handler)
    )


class TestWellKnownDocuments:
    async def test_the_card_is_fetched_from_the_well_known_path_without_a_token(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json=_CARD, headers={"etag": '"abc"'})

        response = await _transport(handler).fetch_card()

        (request,) = seen
        assert (request.method, str(request.url)) == (
            "GET",
            f"{_BASE_URL}/.well-known/agent-card.json",
        )
        assert request.headers["accept"] == "application/json"
        assert "authorization" not in request.headers
        assert (response.status_code, response.json_body) == (200, _CARD)
        assert response.headers["etag"] == '"abc"'

    async def test_the_manifest_is_fetched_from_the_asap_well_known_path(self) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(404)

        transport = A2AHttpTransport(f"{_BASE_URL}/", http_transport=httpx.MockTransport(handler))
        response = await transport.fetch_manifest()

        assert seen == [f"{_BASE_URL}/.well-known/asap/manifest.json"]
        assert (response.status_code, response.json_body) == (404, None)

    @pytest.mark.parametrize("content", [b"not json", b"[1, 2]", b'"text"'])
    async def test_a_body_that_is_not_a_json_object_has_no_json_body(self, content: bytes) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=content)

        response = await _transport(handler).fetch_card()

        assert response.json_body is None
        assert response.body_text == content.decode()

    async def test_a_redirect_comes_back_as_it_is(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"location": "https://elsewhere.example/card"})

        response = await _transport(handler).fetch_card()

        assert response.status_code == 302

    async def test_each_exchange_has_its_own_number(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_CARD)

        transport = _transport(handler)
        first, second = await transport.fetch_card(), await transport.fetch_manifest()

        assert first.request_id != second.request_id


class TestNoCompleteAnswer:
    """One error type, shared with the MCP transport, and no header in its text."""

    async def test_a_network_error_raises_with_its_type_only(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError(f"refused {request.headers}", request=request)

        with pytest.raises(A2AHttpTransportError, match="ConnectError") as excinfo:
            await _transport(handler).fetch_card()

        assert "accept" not in str(excinfo.value)
        assert issubclass(A2AHttpTransportError, HttpExchangeError)
        assert issubclass(McpHttpTransportError, HttpExchangeError)

    async def test_an_answer_slower_than_the_deadline_raises(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(5)
            return httpx.Response(200, json=_CARD)

        with pytest.raises(A2AHttpTransportError, match="within 0.05s"):
            await _transport(handler, timeout_seconds=0.05).fetch_card()

    async def test_a_body_over_the_size_cap_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"x" * (MAX_RESPONSE_BYTES + 1))

        with pytest.raises(A2AHttpTransportError, match=str(MAX_RESPONSE_BYTES)):
            await _transport(handler).fetch_card()
