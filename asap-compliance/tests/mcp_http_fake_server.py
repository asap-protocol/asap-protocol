"""Fake HTTP MCP server for the ``mcp-auth-http`` profile tests.

It answers like ``@asap-protocol/mcp-auth`` in its README's error-code table, and
like ``examples/mcp_auth_http`` did in a live run. Each ``Fault`` breaks one behavior,
so each profile check has a passing and a failing server.
"""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Any

import httpx

PUBLIC_TOOL = "echo"
_RESOURCE_METADATA = 'resource_metadata="http://mcp.test/.well-known/oauth-protected-resource/mcp"'


class Fault(StrEnum):
    """One way to break the server."""

    PUBLIC_TOOL_NEEDS_TOKEN = "public_tool_needs_token"
    AUTH_REQUIRED_IN_TOOL_RESULT = "auth_required_in_tool_result"
    INVALID_TOKEN_IN_TOOL_RESULT = "invalid_token_in_tool_result"
    ANSWERS_200_TO_EVERYTHING = "answers_200_to_everything"


class FakeMcpAuthServer:
    """Handler for ``httpx.MockTransport``."""

    def __init__(self, *faults: Fault) -> None:
        self.faults = frozenset(faults)

    def handle(self, request: httpx.Request) -> httpx.Response:
        call: dict[str, Any] = json.loads(request.content)
        authorization = request.headers.get("authorization")
        if Fault.ANSWERS_200_TO_EVERYTHING in self.faults:
            return _tool_result(call, "ok")
        if authorization is None:
            return self._without_token(call)
        return self._with_token(call, authorization.removeprefix("Bearer "))

    def _without_token(self, call: dict[str, Any]) -> httpx.Response:
        public = call["params"]["name"] == PUBLIC_TOOL
        if public and Fault.PUBLIC_TOOL_NEEDS_TOKEN not in self.faults:
            return _tool_result(call, "ok")
        if Fault.AUTH_REQUIRED_IN_TOOL_RESULT in self.faults:
            return _tool_result(call, "asap:auth_required", is_error=True)
        return self._challenge(401, "invalid_token", "asap:auth_required", error_param=False)

    def _with_token(self, call: dict[str, Any], token: str) -> httpx.Response:
        if Fault.INVALID_TOKEN_IN_TOOL_RESULT in self.faults:
            return _tool_result(call, "asap:invalid_token", is_error=True)
        return self._challenge(401, "invalid_token", "asap:invalid_token: malformed token")

    def _challenge(
        self, status: int, error: str, description: str, *, error_param: bool = True
    ) -> httpx.Response:
        params = [f'error="{error}"', f'error_description="{description}"'] if error_param else []
        headers = {"www-authenticate": "Bearer " + ", ".join([*params, _RESOURCE_METADATA])}
        body = {"error": error, "error_description": description}
        return httpx.Response(status, json=body, headers=headers)


def _tool_result(call: dict[str, Any], text: str, *, is_error: bool = False) -> httpx.Response:
    result: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
    if is_error:
        result["isError"] = True
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": call["id"], "result": result})
