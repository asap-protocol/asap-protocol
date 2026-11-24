"""Fake HTTP MCP server for the ``mcp-auth-http`` profile tests.

It answers like ``@asap-protocol/mcp-auth`` in its README's error-code table, and
like ``examples/mcp_auth_http`` did in a live run. Each ``Fault`` breaks one behavior,
so each profile check has a passing and a failing server.
"""

from __future__ import annotations

import json
import secrets
from enum import StrEnum
from typing import Any

import httpx

PUBLIC_TOOL = "echo"
PROTECTED_TOOL = "secure_action"
PROBE_TOKENS_PATH = "/compliance/probe-tokens"
_RESOURCE_METADATA = 'resource_metadata="http://mcp.test/.well-known/oauth-protected-resource/mcp"'


class Fault(StrEnum):
    """One way to break the server."""

    PUBLIC_TOOL_NEEDS_TOKEN = "public_tool_needs_token"
    AUTH_REQUIRED_IN_TOOL_RESULT = "auth_required_in_tool_result"
    INVALID_TOKEN_IN_TOOL_RESULT = "invalid_token_in_tool_result"
    ACCEPTS_UNSIGNED_JWT = "accepts_unsigned_jwt"
    NO_WWW_AUTHENTICATE = "no_www_authenticate"
    ECHOES_TOKEN_IN_BODY = "echoes_token_in_body"
    ECHOES_TOKEN_IN_HEADER = "echoes_token_in_header"
    REFUSES_VALID_TOKENS = "refuses_valid_tokens"
    ACCEPTS_REPLAY = "accepts_replay"
    EXTERNAL_SINGLE_USE = "external_single_use"
    RUNS_UNDECLARED_TOOL = "runs_undeclared_tool"
    ANSWERS_200_TO_EVERYTHING = "answers_200_to_everything"


class FakeMcpAuthServer:
    """Handler for ``httpx.MockTransport``; ``received_tokens`` lists each bearer token.

    ``POST /compliance/probe-tokens`` mints a fresh set, like the example's compliance
    mode. A token's kind is ``agent`` or ``external``, with ``_out_of_grant`` or not.
    """

    def __init__(self, *faults: Fault) -> None:
        self.faults = frozenset(faults)
        self.received_tokens: list[str] = []
        self._issued: dict[str, str] = {}
        self._used: set[str] = set()

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == PROBE_TOKENS_PATH:
            return httpx.Response(200, json=self._probe_token_set())
        call: dict[str, Any] = json.loads(request.content)
        authorization = request.headers.get("authorization")
        if Fault.ANSWERS_200_TO_EVERYTHING in self.faults:
            return _tool_result(call, "ok")
        if authorization is None:
            return self._without_token(call)
        token = authorization.removeprefix("Bearer ")
        self.received_tokens.append(token)
        response = self._with_token(call, token)
        if Fault.ECHOES_TOKEN_IN_HEADER in self.faults:
            response.headers["x-received-token"] = token
        return response

    def _without_token(self, call: dict[str, Any]) -> httpx.Response:
        public = call["params"]["name"] == PUBLIC_TOOL
        if public and Fault.PUBLIC_TOOL_NEEDS_TOKEN not in self.faults:
            return _tool_result(call, "ok")
        if Fault.AUTH_REQUIRED_IN_TOOL_RESULT in self.faults:
            return _tool_result(call, "asap:auth_required", is_error=True)
        return self._challenge(401, "invalid_token", "asap:auth_required", error_param=False)

    def _with_token(self, call: dict[str, Any], token: str) -> httpx.Response:
        echo = f": got {token}" if Fault.ECHOES_TOKEN_IN_BODY in self.faults else ""
        kind = self._issued.get(token)
        if Fault.ACCEPTS_UNSIGNED_JWT in self.faults and token.count(".") == 2:
            kind = "agent"
        if kind is None or self._refuses(token, kind):
            if Fault.INVALID_TOKEN_IN_TOOL_RESULT in self.faults:
                return _tool_result(call, f"asap:invalid_token{echo}", is_error=True)
            return self._challenge(401, "invalid_token", f"asap:invalid_token{echo}")
        if call["params"]["name"] not in (PUBLIC_TOOL, PROTECTED_TOOL):
            return self._undeclared_tool(call)
        return _tool_result(call, f"executed{echo}")

    def _refuses(self, token: str, kind: str) -> bool:
        """The token layer: an Agent JWT is single-use; an access token is not."""
        single_use = kind.startswith("agent") or Fault.EXTERNAL_SINGLE_USE in self.faults
        replayed = single_use and token in self._used and Fault.ACCEPTS_REPLAY not in self.faults
        self._used.add(token)
        return replayed or Fault.REFUSES_VALID_TOKENS in self.faults

    def _undeclared_tool(self, call: dict[str, Any]) -> httpx.Response:
        if Fault.RUNS_UNDECLARED_TOOL in self.faults:
            error = {"code": -32602, "message": "Tool not found"}
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": call["id"], "error": error})
        return self._challenge(403, "insufficient_scope", "asap:capability_denied")

    def _probe_token_set(self) -> dict[str, Any]:
        def mint(kind: str) -> str:
            token = f"probe-{kind}-{len(self._issued)}-{secrets.token_hex(12)}"
            self._issued[token] = kind
            return token

        agent_jwt = {"valid": mint("agent"), "out_of_grant": mint("agent_out_of_grant")}
        external = {"valid": mint("external"), "out_of_grant": mint("external_out_of_grant")}
        return {"test_only": True, "agent_jwt": agent_jwt, "external": external}

    def _challenge(
        self, status: int, error: str, description: str, *, error_param: bool = True
    ) -> httpx.Response:
        params = [f'error="{error}"', f'error_description="{description}"'] if error_param else []
        headers = {"www-authenticate": "Bearer " + ", ".join([*params, _RESOURCE_METADATA])}
        if Fault.NO_WWW_AUTHENTICATE in self.faults:
            headers = {}
        body = {"error": error, "error_description": description}
        return httpx.Response(status, json=body, headers=headers)


def _tool_result(call: dict[str, Any], text: str, *, is_error: bool = False) -> httpx.Response:
    result: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
    if is_error:
        result["isError"] = True
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": call["id"], "result": result})
