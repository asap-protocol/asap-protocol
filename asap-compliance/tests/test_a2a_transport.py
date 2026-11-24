"""Tests for the plain HTTP A2A transport of the ``a2a-bridge`` profile."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.parse import urljoin

import httpx
import pytest

from asap.adapters.a2a import ASAP_EXTENSION_URI

from asap_compliance.validators.a2a_transport import (
    MAX_RESPONSE_BYTES,
    A2AHttpTransport,
    A2AHttpTransportError,
)
from asap_compliance.validators.http_answers import AUTH_REQUIRED_401
from asap_compliance.validators.http_exchange import HttpExchangeError
from asap_compliance.validators.mcp_http_transport import McpHttpTransportError
from asap_compliance.validators.probe_tokens import ComplianceEndpointTokenProvider, ProbeTokenKind

_BASE_URL = "https://gateway.example"
_CARD = {"name": "A2A Gateway Demo", "version": "1.0.0"}
_SECRET = "secret-probe-token-value"


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


def _rpc_answer(request: httpx.Request, **member: Any) -> httpx.Response:
    request_id = json.loads(request.content)["id"]
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": request_id, **member})


class TestSendMessage:
    """One ``SendMessage`` in the A2A binding (JSON-RPC at ``/a2a``, ``A2A-Version: 1.0``)."""

    async def test_sends_one_post_that_selects_the_skill_with_one_data_part(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return _rpc_answer(request, result={"message": {"parts": [{"data": {}}]}})

        response = await _transport(handler).send_message(
            "greet", {"name": "Ada"}, bearer_token="probe-token"
        )

        (request,) = seen
        assert (request.method, str(request.url)) == ("POST", f"{_BASE_URL}/a2a")
        assert request.headers["a2a-version"] == "1.0"
        assert request.headers["content-type"] == "application/json"
        assert request.headers["authorization"] == "Bearer probe-token"
        body = json.loads(request.content)
        message = body["params"]["message"]
        assert (body["jsonrpc"], body["id"], body["method"]) == (
            "2.0",
            response.request_id,
            "SendMessage",
        )
        assert (message["role"], message["parts"]) == ("ROLE_USER", [{"data": {"name": "Ada"}}])
        assert message["metadata"] == {ASAP_EXTENSION_URI: {"skill_id": "greet"}}
        assert response.rpc_result == {"message": {"parts": [{"data": {}}]}}

    async def test_each_call_has_a_new_id_and_message_id_and_no_token_means_no_header(
        self,
    ) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(401, json={"error_description": "asap:auth_required"})

        transport = _transport(handler)
        await transport.send_message("greet", {})
        await transport.send_message("greet", {})

        bodies = [json.loads(request.content) for request in seen]
        assert "authorization" not in seen[0].headers
        assert bodies[0]["id"] != bodies[1]["id"]
        assert (
            bodies[0]["params"]["message"]["messageId"]
            != (bodies[1]["params"]["message"]["messageId"])
        )


class TestJsonRpcAnswer:
    @pytest.mark.parametrize(
        ("member", "result", "error"),
        [
            ({"result": {"message": {}}}, {"message": {}}, None),
            ({"error": {"code": -32602, "message": "x"}}, None, {"code": -32602, "message": "x"}),
        ],
    )
    async def test_a_result_or_an_error_for_this_request_is_read(
        self, member: dict[str, Any], result: Any, error: Any
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _rpc_answer(request, **member)

        response = await _transport(handler).send_message("greet", {})

        assert (response.rpc_result, response.rpc_error) == (result, error)

    @pytest.mark.parametrize("answer_id", [999, True, "1", None])
    async def test_an_answer_to_another_request_is_not_read(self, answer_id: Any) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            body = {"jsonrpc": "2.0", "id": answer_id, "result": {"message": {}}}
            return httpx.Response(200, json=body)

        response = await _transport(handler).send_message("greet", {})

        assert response.request_id == 1
        assert (response.rpc_result, response.rpc_error) == (None, None)

    @pytest.mark.parametrize(
        ("status", "member", "described"),
        [
            (200, {"result": {"message": {}}}, "HTTP 200, a JSON-RPC result"),
            (
                200,
                {"error": {"code": -32602, "message": _SECRET}},
                "HTTP 200, JSON-RPC error -32602",
            ),
            (
                403,
                {"error_description": f"asap:constraint_violation: {_SECRET}"},
                "HTTP 403, no JSON-RPC answer, 'asap:constraint_violation'",
            ),
        ],
    )
    async def test_the_description_names_the_layer_and_never_server_text(
        self, status: int, member: dict[str, Any], described: str
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            body = {"jsonrpc": "2.0", "id": json.loads(request.content)["id"], **member}
            return httpx.Response(status, json=body)

        response = await _transport(handler).send_message("greet", {})

        assert response.describe() == described

    async def test_a_network_error_does_not_hold_the_token(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError(str(request.headers), request=request)

        with pytest.raises(A2AHttpTransportError) as excinfo:
            await _transport(handler).send_message("greet", {}, bearer_token=_SECRET)

        assert _SECRET not in str(excinfo.value)


@pytest.mark.a2a_gateway_live
class TestLiveGateway:
    """Against ``python -m examples.a2a_gateway.serve --compliance`` (CI: live flows)."""

    async def test_card_manifest_refusal_and_one_call(self, live_a2a_gateway_url: str) -> None:
        transport = A2AHttpTransport(live_a2a_gateway_url)
        provider = ComplianceEndpointTokenProvider(
            urljoin(live_a2a_gateway_url, "/compliance/probe-tokens")
        )

        card = await transport.fetch_card()
        manifest = await transport.fetch_manifest()
        refused = await transport.send_message("greet", {"name": "Ada", "language": "en"})
        token = await provider.probe_token(ProbeTokenKind.EXTERNAL)
        allowed = await transport.send_message(
            "greet", {"name": "Ada", "language": "en"}, bearer_token=token
        )

        assert card.json_body is not None and manifest.json_body is not None
        assert card.json_body["supportedInterfaces"][0]["url"] == transport.a2a_url
        assert manifest.json_body["id"] == "urn:asap:agent:a2a-gateway-demo"
        assert AUTH_REQUIRED_401.problem(refused) is None
        assert allowed.rpc_result is not None
        assert allowed.rpc_result["message"]["parts"] == [{"data": {"greeting": "Hello, Ada!"}}]
