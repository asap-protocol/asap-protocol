"""Plain HTTP MCP transport for the ``mcp-auth-http`` compliance profile.

One ``tools/call`` is one Streamable HTTP POST in the MCP ``2026-07-28`` form: no
``initialize`` and no session. The headers and ``_meta`` keys are the ones recorded
in ``examples/mcp_auth_http/README.md`` ("Call a tool without an MCP SDK").
No MCP SDK: the harness judges the raw answer.

The body is parsed when it is ``application/json``, or ``text/event-stream`` (the
JSON-RPC response is taken from the events; notifications are skipped). Each call has
one deadline for the whole answer and a body size cap, and never follows a redirect.

Example::

    transport = McpHttpTransport("http://127.0.0.1:8787/mcp")
    response = await transport.call_tool("echo", {"message": "hi"})
    assert response.status_code == 200 and response.tool_result is not None
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from asap_compliance.validators.http_answers import quoted_code
from asap_compliance.validators.http_exchange import HttpExchangeError, bounded_http_exchange

MCP_HTTP_PROTOCOL_VERSION = "2026-07-28"
# A tools/call answer is small; the cap stops a server that never ends its body.
MAX_RESPONSE_BYTES = 1_048_576
# SDK 2.4.0 does not require Accept, but a response can switch to SSE.
_ACCEPT = "application/json, text/event-stream"


class McpHttpTransportError(HttpExchangeError):
    """A ``tools/call`` POST got no complete HTTP answer (network error, timeout, or size)."""


@dataclass(frozen=True)
class McpHttpResponse:
    """One HTTP answer to a ``tools/call`` POST, as the server sent it."""

    request_id: int
    status_code: int
    headers: httpx.Headers
    body_text: str
    json_body: dict[str, Any] | None

    @property
    def tool_result(self) -> dict[str, Any] | None:
        """The JSON-RPC ``result`` when the body is the response to this request, else ``None``."""
        body = self.json_body
        if body is None or body.get("jsonrpc") != "2.0" or "error" in body:
            return None
        response_id = body.get("id")
        if isinstance(response_id, bool) or response_id != self.request_id:
            return None
        result = body.get("result")
        return result if isinstance(result, dict) else None

    def describe(self) -> str:
        """Status, layer, and ``asap:*`` code of this answer, for a check message."""
        result = self.tool_result
        if result is None:
            description = (self.json_body or {}).get("error_description")
            return f"HTTP {self.status_code}, no tool result, {quoted_code(description)}"
        layer = "tool error result" if result.get("isError") is True else "tool result"
        return f"HTTP {self.status_code}, {layer}, {quoted_code(first_text(result))}"


def first_text(result: Mapping[str, Any]) -> object:
    """The ``text`` of a tool result's first content item, when that item is text."""
    content = result.get("content")
    if not isinstance(content, list) or not content or not isinstance(content[0], dict):
        return None
    return content[0].get("text") if content[0].get("type") == "text" else None


class McpHttpTransport:
    """Send ``tools/call`` to one MCP endpoint over plain HTTP; never follow a redirect."""

    def __init__(
        self,
        mcp_url: str,
        *,
        timeout_seconds: float = 10.0,
        http_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._mcp_url = mcp_url
        self._timeout_seconds = timeout_seconds
        self._http_transport = http_transport
        self._request_ids = itertools.count(1)

    async def call_tool(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        bearer_token: str | None = None,
    ) -> McpHttpResponse:
        """POST one ``tools/call``; raise :class:`McpHttpTransportError` if no full answer came."""
        request_id = next(self._request_ids)
        headers = _tools_call_headers(tool_name, bearer_token)
        body = _tools_call_body(request_id, tool_name, arguments)
        answer = await bounded_http_exchange(
            "POST",
            self._mcp_url,
            headers=headers,
            json_body=body,
            timeout_seconds=self._timeout_seconds,
            max_bytes=MAX_RESPONSE_BYTES,
            http_transport=self._http_transport,
            where=f"tools/call {tool_name!r} to {self._mcp_url}",
            error_type=McpHttpTransportError,
        )
        text = answer.body.decode("utf-8", errors="replace")
        json_body = _parse_body(answer.headers, text, request_id)
        return McpHttpResponse(request_id, answer.status_code, answer.headers, text, json_body)


def _tools_call_headers(tool_name: str, bearer_token: str | None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Accept": _ACCEPT,
        "MCP-Protocol-Version": MCP_HTTP_PROTOCOL_VERSION,
        "Mcp-Method": "tools/call",
        "Mcp-Name": tool_name,
    }
    if bearer_token is not None:
        headers["Authorization"] = f"Bearer {bearer_token}"
    return headers


def _tools_call_body(
    request_id: int, tool_name: str, arguments: Mapping[str, Any]
) -> dict[str, Any]:
    # Both _meta keys are required; a missing one is HTTP 400 -32602.
    meta = {
        "io.modelcontextprotocol/protocolVersion": MCP_HTTP_PROTOCOL_VERSION,
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    params = {"name": tool_name, "arguments": dict(arguments), "_meta": meta}
    return {"jsonrpc": "2.0", "id": request_id, "method": "tools/call", "params": params}


def _parse_body(headers: httpx.Headers, text: str, request_id: int) -> dict[str, Any] | None:
    media_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type == "application/json":
        return _json_object(text)
    if media_type == "text/event-stream":
        return _sse_response_message(text, request_id)
    return None


def _json_object(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
    except (ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def _sse_response_message(text: str, request_id: int) -> dict[str, Any] | None:
    """The JSON-RPC response to ``request_id`` in an SSE body; notifications are skipped."""
    for data in _sse_event_data(text):
        message = _json_object(data)
        if message is None or message.get("id") != request_id:
            continue
        if "result" in message or "error" in message:
            return message
    return None


def _sse_event_data(text: str) -> list[str]:
    """The ``data`` of each event, per WHATWG HTML "Interpreting an event stream".

    Only ``data`` fields matter here. An event ends at a blank line; data still pending
    when the stream ends is dropped, as the standard says.
    """
    events: list[str] = []
    data_lines: list[str] = []
    # The text after the last line end is not a complete line, so it is dropped too.
    complete_lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")[:-1]
    for line in complete_lines:
        if line == "":
            if data_lines:
                events.append("\n".join(data_lines))
            data_lines = []
        elif line.startswith("data:"):
            data_lines.append(line.removeprefix("data:").removeprefix(" "))
    return events
