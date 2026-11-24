"""Tests for the answer readers that the HTTP profiles share.

``mcp-auth-http`` and ``a2a-bridge`` judge a refusal in the same HTTP form, and
each binding's answer describes its own layer for a check message.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from asap_compliance.validators import http_answers, mcp_http_answers
from asap_compliance.validators.http_answers import (
    AUTH_REQUIRED_401,
    CAPABILITY_DENIED_403,
    INVALID_TOKEN_401,
    BearerChallenge,
    HttpAnswer,
    has_bearer_challenge,
    has_code,
    quoted_code,
    quoted_word,
)
from asap_compliance.validators.mcp_http_transport import McpHttpResponse

_SECRET = "secret-probe-token-value"
_INVALID = 'Bearer error="invalid_token", error_description="asap:invalid_token"'
_SCOPE = 'Bearer error="insufficient_scope", error_description="asap:capability_denied"'


@dataclass(frozen=True)
class _OtherBindingAnswer:
    """An answer of a binding that is not MCP: the protocol members only."""

    status_code: int
    headers: httpx.Headers
    json_body: dict[str, Any] | None

    def describe(self) -> str:
        return f"HTTP {self.status_code}, other binding"


def _refusal(status: int, error: str, code: str, challenge: str | None) -> _OtherBindingAnswer:
    headers = httpx.Headers({} if challenge is None else {"www-authenticate": challenge})
    return _OtherBindingAnswer(status, headers, {"error": error, "error_description": code})


class TestAnswerText:
    @pytest.mark.parametrize(
        ("text", "quoted"),
        [
            (f"asap:invalid_token: {_SECRET}", "'asap:invalid_token'"),
            (_SECRET, "no asap:* code"),
            (None, "no asap:* code"),
        ],
    )
    def test_only_an_asap_code_is_quoted(self, text: object, quoted: str) -> None:
        assert quoted_code(text) == quoted

    def test_a_word_that_could_hold_a_token_is_not_quoted(self) -> None:
        assert (quoted_word("invalid_token"), quoted_word(_SECRET)) == (
            "'invalid_token'",
            "another value",
        )

    def test_a_code_is_found_alone_or_before_a_detail(self) -> None:
        code = "asap:invalid_token"

        assert has_code(code, code) and has_code(f"{code}: replay", code)
        assert not has_code(f"{code}s", code) and not has_code(f"x {code}", code)


class TestMcpAnswerDescription:
    def test_an_mcp_answer_describes_its_own_layer(self) -> None:
        content = [{"type": "text", "text": f"asap:invalid_token: {_SECRET}"}]
        body = {"jsonrpc": "2.0", "id": 1, "result": {"isError": True, "content": content}}
        answer = McpHttpResponse(1, 200, httpx.Headers(), json.dumps(body), body)

        assert answer.describe() == "HTTP 200, tool error result, 'asap:invalid_token'"
        assert mcp_http_answers.describe_answer(answer) == answer.describe()


class TestRefusalsInAnyBinding:
    @pytest.mark.parametrize(
        ("answer", "expected"),
        [
            (_refusal(401, "invalid_token", "asap:auth_required", "Bearer"), AUTH_REQUIRED_401),
            (_refusal(401, "invalid_token", "asap:invalid_token", _INVALID), INVALID_TOKEN_401),
            (
                _refusal(403, "insufficient_scope", "asap:capability_denied", _SCOPE),
                CAPABILITY_DENIED_403,
            ),
        ],
    )
    def test_the_s5_and_s6_refusals_pass_for_an_answer_of_another_binding(
        self, answer: HttpAnswer, expected: BearerChallenge
    ) -> None:
        assert expected.problem(answer) is None
        assert has_bearer_challenge(answer)

    def test_a_wrong_status_is_told_with_the_answer_s_own_description(self) -> None:
        answer = _OtherBindingAnswer(200, httpx.Headers(), {"result": _SECRET})

        assert INVALID_TOKEN_401.problem(answer) == "got HTTP 200, other binding"

    def test_a_refusal_that_holds_a_secret_is_not_quoted(self) -> None:
        answer = _refusal(401, _SECRET, f"asap:invalid_token: {_SECRET}", f"Bearer error={_SECRET}")

        problem = INVALID_TOKEN_401.problem(answer)

        assert problem is not None and _SECRET not in problem

    def test_the_mcp_answer_module_hands_out_the_same_readers(self) -> None:
        assert mcp_http_answers.BearerChallenge is http_answers.BearerChallenge
        assert mcp_http_answers.INVALID_TOKEN_401 is INVALID_TOKEN_401
        assert mcp_http_answers.has_bearer_challenge is has_bearer_challenge
