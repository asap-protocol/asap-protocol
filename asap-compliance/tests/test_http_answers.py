"""Tests for the answer readers that the HTTP profiles share.

``mcp-auth-http`` and ``a2a-bridge`` judge a refusal in the same HTTP form, and
each binding's answer describes its own layer for a check message.
"""

from __future__ import annotations

import json

import httpx
import pytest

from asap_compliance.validators import mcp_http_answers
from asap_compliance.validators.http_answers import has_code, quoted_code, quoted_word
from asap_compliance.validators.mcp_http_transport import McpHttpResponse

_SECRET = "secret-probe-token-value"


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
