"""Tests for the plain HTTP MCP transport of the ``mcp-auth-http`` profile."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from asap_compliance.validators.mcp_http_transport import (
    MAX_RESPONSE_BYTES,
    McpHttpTransport,
    McpHttpTransportError,
)

_MCP_URL = "http://mcp.test/mcp"
_META = {
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientCapabilities": {},
}


def _request_id(request: httpx.Request) -> Any:
    return json.loads(request.content)["id"]


def _tool_result_response(request: httpx.Request, result: dict[str, Any]) -> httpx.Response:
    return httpx.Response(
        200, json={"jsonrpc": "2.0", "id": _request_id(request), "result": result}
    )


def _transport(handler: Any, *, timeout_seconds: float = 5.0) -> McpHttpTransport:
    return McpHttpTransport(
        _MCP_URL, timeout_seconds=timeout_seconds, http_transport=httpx.MockTransport(handler)
    )


class TestToolsCallRequest:
    """What the transport sends as a non-SDK client."""

    async def test_sends_one_post_in_the_2026_07_28_form(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return _tool_result_response(request, {"content": []})

        await _transport(handler).call_tool("echo", {"message": "hi"})

        (request,) = seen
        assert request.method == "POST"
        assert str(request.url) == _MCP_URL
        assert request.headers["content-type"] == "application/json"
        assert request.headers["accept"] == "application/json, text/event-stream"
        assert request.headers["mcp-protocol-version"] == "2026-07-28"
        assert request.headers["mcp-method"] == "tools/call"
        assert request.headers["mcp-name"] == "echo"
        assert "authorization" not in request.headers
        assert json.loads(request.content) == {
            "jsonrpc": "2.0",
            "id": _request_id(request),
            "method": "tools/call",
            "params": {"name": "echo", "arguments": {"message": "hi"}, "_meta": _META},
        }

    async def test_sends_the_token_as_a_bearer_header(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return _tool_result_response(request, {"content": []})

        await _transport(handler).call_tool("secure_action", {}, bearer_token="probe-token")

        assert seen[0].headers["authorization"] == "Bearer probe-token"

    async def test_each_call_has_its_own_request_id(self) -> None:
        transport = _transport(lambda request: _tool_result_response(request, {"content": []}))

        first = await transport.call_tool("echo", {})
        second = await transport.call_tool("echo", {})

        assert first.request_id != second.request_id
        assert first.tool_result == second.tool_result == {"content": []}


class TestToolsCallResponse:
    """What the transport returns: status, headers, and the parsed body."""

    async def test_returns_status_headers_and_the_tool_result(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            response = _tool_result_response(request, {"content": [{"type": "text", "text": "hi"}]})
            response.headers["x-probe"] = "1"
            return response

        response = await _transport(handler).call_tool("echo", {"message": "hi"})

        assert response.status_code == 200
        assert response.headers["x-probe"] == "1"
        assert response.tool_result == {"content": [{"type": "text", "text": "hi"}]}

    async def test_returns_an_http_error_answer_as_it_came(self) -> None:
        body = {"error": "invalid_token", "error_description": "asap:auth_required"}

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json=body, headers={"www-authenticate": "Bearer"})

        response = await _transport(handler).call_tool("secure_action", {})

        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
        assert response.json_body == body
        assert response.tool_result is None

    async def test_tool_result_is_none_when_the_body_answers_another_request(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 999, "result": {}})

        response = await _transport(handler).call_tool("echo", {})

        assert response.json_body is not None
        assert response.tool_result is None

    @pytest.mark.parametrize(
        ("content_type", "content"),
        [("text/plain", b"ok"), ("application/json", b"{not json"), ("application/json", b"[1]")],
    )
    async def test_a_body_that_is_not_a_json_object_is_not_parsed(
        self, content_type: str, content: bytes
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=content, headers={"content-type": content_type})

        response = await _transport(handler).call_tool("echo", {})

        assert response.body_text == content.decode()
        assert response.json_body is None
        assert response.tool_result is None

    async def test_does_not_follow_a_redirect(self) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.path)
            return httpx.Response(307, headers={"location": "http://mcp.test/elsewhere"})

        response = await _transport(handler).call_tool("secure_action", {}, bearer_token="t")

        assert response.status_code == 307
        assert seen == ["/mcp"]


def _sse_response(stream: str) -> httpx.Response:
    return httpx.Response(
        200, content=stream.encode(), headers={"content-type": "text/event-stream"}
    )


class TestServerSentEventsResponse:
    """A response can switch to SSE when a tool notifies first."""

    async def test_takes_the_response_to_this_request_after_a_notification(self) -> None:
        notification = {"jsonrpc": "2.0", "method": "notifications/progress", "params": {}}

        def handler(request: httpx.Request) -> httpx.Response:
            answer = {"jsonrpc": "2.0", "id": _request_id(request), "result": {"content": []}}
            return _sse_response(
                f"event: message\ndata: {json.dumps(notification)}\n\n"
                f"event: message\ndata: {json.dumps(answer)}\n\n"
            )

        response = await _transport(handler).call_tool("echo", {})

        assert response.tool_result == {"content": []}
        assert "notifications/progress" in response.body_text

    async def test_joins_data_lines_and_reads_crlf_line_ends(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            head = f'{{"jsonrpc": "2.0", "id": {_request_id(request)},'
            return _sse_response(f'data: {head}\r\ndata: "result": {{"content": []}}}}\r\n\r\n')

        response = await _transport(handler).call_tool("echo", {})

        assert response.tool_result == {"content": []}

    async def test_an_event_that_is_never_ended_is_not_read(self) -> None:
        # WHATWG "Interpreting an event stream": pending data at the end of the stream is dropped.
        def handler(request: httpx.Request) -> httpx.Response:
            answer = {"jsonrpc": "2.0", "id": _request_id(request), "result": {"content": []}}
            return _sse_response(f"data: {json.dumps(answer)}\n")

        response = await _transport(handler).call_tool("echo", {})

        assert response.json_body is None
        assert response.tool_result is None


class TestToolsCallFailure:
    """No complete HTTP answer: one error type, and no token in its text."""

    async def test_network_error_raises_without_the_token(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        with pytest.raises(McpHttpTransportError, match="ConnectError") as excinfo:
            await _transport(handler).call_tool("secure_action", {}, bearer_token="secret-probe")

        assert "secret-probe" not in str(excinfo.value)

    async def test_an_answer_slower_than_the_timeout_raises(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(5)
            return _tool_result_response(request, {"content": []})

        with pytest.raises(McpHttpTransportError, match="within 0.05s"):
            await _transport(handler, timeout_seconds=0.05).call_tool("echo", {})

    async def test_a_body_over_the_size_cap_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"x" * (MAX_RESPONSE_BYTES + 1))

        with pytest.raises(McpHttpTransportError, match=str(MAX_RESPONSE_BYTES)):
            await _transport(handler).call_tool("echo", {})


@pytest.mark.mcp_http_live
class TestLiveExampleServer:
    """Against ``examples/mcp_auth_http`` started with ``--compliance`` (CI: live flows)."""

    async def test_public_tool_and_missing_token_answers(self, live_mcp_http_url: str) -> None:
        transport = McpHttpTransport(live_mcp_http_url)

        public = await transport.call_tool("echo", {"message": "asap-compliance"})
        protected = await transport.call_tool("secure_action", {"action": "deploy"})

        assert public.status_code == 200
        assert public.tool_result is not None
        assert public.tool_result["content"] == [{"type": "text", "text": "asap-compliance"}]
        assert protected.status_code == 401
        assert protected.headers["www-authenticate"].startswith("Bearer")
        assert protected.json_body is not None
        assert protected.json_body["error_description"] == "asap:auth_required"
