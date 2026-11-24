"""Plain HTTP MCP transport for the ``mcp-auth-http`` compliance profile.

One ``tools/call`` is one Streamable HTTP POST in the MCP ``2026-07-28`` form: no
``initialize`` and no session. The headers and ``_meta`` keys are the ones recorded
in ``examples/mcp_auth_http/README.md`` ("Call a tool without an MCP SDK").
No MCP SDK: the harness judges the raw answer.

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

MCP_HTTP_PROTOCOL_VERSION = "2026-07-28"
# SDK 2.4.0 does not require Accept, but a response can switch to SSE.
_ACCEPT = "application/json, text/event-stream"


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
        if body is None or body.get("jsonrpc") != "2.0" or body.get("id") != self.request_id:
            return None
        result = body.get("result")
        return result if isinstance(result, dict) else None


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
        """POST one ``tools/call`` and return the answer as the server sent it."""
        request_id = next(self._request_ids)
        headers = _tools_call_headers(tool_name, bearer_token)
        body = _tools_call_body(request_id, tool_name, arguments)
        async with httpx.AsyncClient(
            transport=self._http_transport,
            timeout=self._timeout_seconds,
            follow_redirects=False,
        ) as client:
            response = await client.post(self._mcp_url, headers=headers, json=body)
        text = response.content.decode("utf-8", errors="replace")
        json_body = _parse_body(response.headers, text)
        return McpHttpResponse(request_id, response.status_code, response.headers, text, json_body)


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


def _parse_body(headers: httpx.Headers, text: str) -> dict[str, Any] | None:
    media_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type == "application/json":
        return _json_object(text)
    return None


def _json_object(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
    except (ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) else None
