"""Tests for reading one HTTP MCP answer in the ``mcp-auth-http`` profile.

The passing answers are the ones ``examples/mcp_auth_http`` gave in a live run; they
match its README's answer table. Each failing answer has the right code in the
wrong layer, or the right layer with one part wrong.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from asap_compliance.validators.mcp_http_answers import (
    AUTH_REQUIRED_401,
    CAPABILITY_DENIED_403,
    INVALID_TOKEN_401,
    BearerChallenge,
    describe_answer,
    has_bearer_challenge,
    tool_error_problem,
    tool_success_problem,
)
from asap_compliance.validators.mcp_http_transport import McpHttpResponse

_METADATA = 'resource_metadata="http://mcp.test/.well-known/oauth-protected-resource/mcp"'
_SECRET = "secret-probe-token-value"


def _answer(
    status: int, body: dict[str, Any], headers: dict[str, str] | None = None
) -> McpHttpResponse:
    return McpHttpResponse(1, status, httpx.Headers(headers or {}), json.dumps(body), body)


def _tool_answer(text: str, *, is_error: bool = False, request_id: int = 1) -> McpHttpResponse:
    result: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
    if is_error:
        result["isError"] = True
    return _answer(200, {"jsonrpc": "2.0", "id": request_id, "result": result})


def _challenge(status: int, error: str, description: str, header: str | None) -> McpHttpResponse:
    headers = {} if header is None else {"www-authenticate": header}
    return _answer(status, {"error": error, "error_description": description}, headers)


_AUTH_REQUIRED = _challenge(401, "invalid_token", "asap:auth_required", f"Bearer {_METADATA}")
_INVALID_TOKEN = _challenge(
    401,
    "invalid_token",
    "asap:invalid_token: jti replay detected",
    'Bearer error="invalid_token", error_description="asap:invalid_token: jti replay detected", '
    + _METADATA,
)
_CAPABILITY_DENIED = _challenge(
    403,
    "insufficient_scope",
    "asap:capability_denied",
    'Bearer error="insufficient_scope", error_description="asap:capability_denied", '
    f'scope="secure_action:run", {_METADATA}',
)
# RFC 7235: an auth-param value is a token or a quoted string; the scheme ignores case.
_UNQUOTED_ERROR_PARAMETER = _challenge(
    401, "invalid_token", "asap:invalid_token", "bearer error=invalid_token"
)
# One part wrong each.
_ERROR_HEADER = 'Bearer error="invalid_token"'
_NO_CHALLENGE = _challenge(401, "invalid_token", "asap:auth_required", None)
_BASIC_SCHEME = _challenge(401, "invalid_token", "asap:auth_required", "Basic realm=x")
_WRONG_OAUTH_ERROR = _challenge(401, "invalid_request", "asap:invalid_token", _ERROR_HEADER)
_LOOKALIKE_CODE = _challenge(401, "invalid_token", "asap:invalid_tokens", _ERROR_HEADER)


class TestToolResults:
    def test_a_tool_result_that_is_not_an_error_is_a_success(self) -> None:
        assert tool_success_problem(_tool_answer("executed: deploy")) is None

    @pytest.mark.parametrize(
        "answer",
        [
            _tool_answer("asap:capability_denied", is_error=True),
            _tool_answer("executed: deploy", request_id=2),
            _answer(200, {"jsonrpc": "2.0", "id": 1, "error": {"code": -32602, "message": "x"}}),
            _AUTH_REQUIRED,
        ],
    )
    def test_anything_else_is_not_a_success(self, answer: McpHttpResponse) -> None:
        assert tool_success_problem(answer) is not None

    @pytest.mark.parametrize(
        "text", ["asap:constraint_violation", "asap:constraint_violation: action: is forbidden"]
    )
    def test_a_tool_error_with_the_code_first_passes(self, text: str) -> None:
        answer = _tool_answer(text, is_error=True)

        assert tool_error_problem(answer, "asap:constraint_violation") is None

    @pytest.mark.parametrize(
        "answer",
        [
            _tool_answer("asap:constraint_violation"),
            _tool_answer("asap:constraint_violations", is_error=True),
            _tool_answer("denied: asap:constraint_violation", is_error=True),
            _challenge(403, "insufficient_scope", "asap:constraint_violation", "Bearer"),
        ],
    )
    def test_the_code_in_another_place_or_layer_fails(self, answer: McpHttpResponse) -> None:
        assert tool_error_problem(answer, "asap:constraint_violation") is not None


class TestBearerChallenges:
    @pytest.mark.parametrize(
        ("answer", "expected"),
        [
            (_AUTH_REQUIRED, AUTH_REQUIRED_401),
            (_INVALID_TOKEN, INVALID_TOKEN_401),
            (_UNQUOTED_ERROR_PARAMETER, INVALID_TOKEN_401),
            (_CAPABILITY_DENIED, CAPABILITY_DENIED_403),
        ],
    )
    def test_the_answers_of_the_s6_table_pass(
        self, answer: McpHttpResponse, expected: BearerChallenge
    ) -> None:
        assert expected.problem(answer) is None
        assert has_bearer_challenge(answer)

    @pytest.mark.parametrize(
        ("answer", "expected"),
        [
            (_tool_answer("asap:auth_required", is_error=True), AUTH_REQUIRED_401),
            (_NO_CHALLENGE, AUTH_REQUIRED_401),
            (_BASIC_SCHEME, AUTH_REQUIRED_401),
            (_INVALID_TOKEN, AUTH_REQUIRED_401),
            (_AUTH_REQUIRED, INVALID_TOKEN_401),
            (_WRONG_OAUTH_ERROR, INVALID_TOKEN_401),
            (_LOOKALIKE_CODE, INVALID_TOKEN_401),
            (_CAPABILITY_DENIED, INVALID_TOKEN_401),
            (_tool_answer("asap:capability_denied", is_error=True), CAPABILITY_DENIED_403),
        ],
    )
    def test_a_wrong_layer_status_challenge_or_body_fails(
        self, answer: McpHttpResponse, expected: BearerChallenge
    ) -> None:
        assert expected.problem(answer) is not None


class TestNothingButAnAsapCodeIsQuoted:
    @pytest.mark.parametrize(
        "answer",
        [
            _tool_answer(f"asap:invalid_token: {_SECRET}", is_error=True),
            _challenge(401, _SECRET, f"asap:invalid_token: {_SECRET}", f'Bearer error="{_SECRET}"'),
            _challenge(403, "x", _SECRET, f"Bearer error={_SECRET}"),
        ],
    )
    def test_server_text_is_not_quoted(self, answer: McpHttpResponse) -> None:
        messages = [
            describe_answer(answer),
            tool_success_problem(answer),
            tool_error_problem(answer, "asap:capability_denied"),
            AUTH_REQUIRED_401.problem(answer),
            INVALID_TOKEN_401.problem(answer),
        ]

        assert all(message is not None and _SECRET not in message for message in messages)

    def test_the_asap_code_and_the_layer_are_quoted(self) -> None:
        answer = _tool_answer(f"asap:invalid_token: {_SECRET}", is_error=True)

        assert describe_answer(answer) == "HTTP 200, tool error result, 'asap:invalid_token'"
