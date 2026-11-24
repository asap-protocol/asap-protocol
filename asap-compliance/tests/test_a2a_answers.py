"""Tests for reading one A2A gateway answer in the ``a2a-bridge`` profile.

The passing answers are the ones ``examples/a2a_gateway`` gave in a live run;
they match its README's refusal table. Each failing answer has the right
code in the wrong layer, or the right layer with one part wrong.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from asap_compliance.validators.a2a_answers import (
    CONSTRAINT_VIOLATION_403,
    INVALID_PARAMS,
    message_success_problem,
    rpc_error_problem,
)
from asap_compliance.validators.a2a_transport import A2AHttpResponse

_SECRET = "secret-probe-token-value"
_SCOPE = 'Bearer error="insufficient_scope", error_description="asap:capability_denied"'


def _answer(
    status: int, body: dict[str, Any], headers: dict[str, str] | None = None
) -> A2AHttpResponse:
    return A2AHttpResponse(1, status, httpx.Headers(headers or {}), json.dumps(body), body)


def _rpc(member: dict[str, Any], *, request_id: object = 1) -> A2AHttpResponse:
    return _answer(200, {"jsonrpc": "2.0", "id": request_id, **member})


def _message(parts: list[dict[str, Any]]) -> dict[str, Any]:
    return {"result": {"message": {"role": "ROLE_AGENT", "messageId": "m-1", "parts": parts}}}


_GREETING = _rpc(_message([{"data": {"greeting": "Hello, Ada!"}}]))
_INVALID_PARAMS = _rpc({"error": {"code": -32602, "message": f"invalid params {_SECRET}"}})
_VIOLATION_BODY = {
    "error": "access_denied",
    "error_description": "asap:constraint_violation",
    "violations": [{"field": "language", "operator": "in"}],
}
_CONSTRAINT_VIOLATION = _answer(403, _VIOLATION_BODY)


class TestMessageSuccess:
    def test_a_result_whose_message_has_a_data_part_is_a_success(self) -> None:
        assert message_success_problem(_GREETING) is None

    @pytest.mark.parametrize(
        "answer",
        [
            _INVALID_PARAMS,
            _rpc(_message([{"text": "Hello"}])),
            _rpc(_message([])),
            _rpc({"result": {"task": {"id": "t-1"}}}),
            _rpc(_message([{"data": {}}]), request_id=2),
            _answer(401, {"error_description": "asap:auth_required"}),
            _answer(203, _message([{"data": {}}])),
        ],
    )
    def test_anything_else_is_not_a_success(self, answer: A2AHttpResponse) -> None:
        assert message_success_problem(answer) is not None


class TestJsonRpcError:
    def test_the_code_at_http_200_for_this_request_passes(self) -> None:
        assert rpc_error_problem(_INVALID_PARAMS, INVALID_PARAMS) is None

    @pytest.mark.parametrize(
        "answer",
        [
            _rpc({"error": {"code": -32004, "message": "x"}}),
            _rpc({"error": {"code": "-32602", "message": "x"}}),
            _rpc({"error": {"code": -32602, "message": "x"}}, request_id=2),
            _GREETING,
            _answer(400, {"jsonrpc": "2.0", "id": 1, "error": {"code": -32602}}),
        ],
    )
    def test_another_code_layer_or_request_fails(self, answer: A2AHttpResponse) -> None:
        assert rpc_error_problem(answer, INVALID_PARAMS) is not None


class TestConstraintViolation:
    def test_the_s5_answer_passes(self) -> None:
        assert CONSTRAINT_VIOLATION_403.problem(_CONSTRAINT_VIOLATION) is None

    @pytest.mark.parametrize(
        "answer",
        [
            _answer(403, _VIOLATION_BODY, {"www-authenticate": _SCOPE}),
            _answer(403, {**_VIOLATION_BODY, "error": "insufficient_scope"}),
            _answer(403, {**_VIOLATION_BODY, "error_description": "asap:constraint_violations"}),
            _answer(401, _VIOLATION_BODY),
            _GREETING,
        ],
    )
    def test_a_challenge_another_body_or_another_status_fails(
        self, answer: A2AHttpResponse
    ) -> None:
        assert CONSTRAINT_VIOLATION_403.problem(answer) is not None


class TestNothingButACodeIsQuoted:
    @pytest.mark.parametrize(
        "answer",
        [
            _INVALID_PARAMS,
            _answer(403, {"error": _SECRET, "error_description": f"asap:x: {_SECRET}"}),
            _answer(403, _VIOLATION_BODY, {"www-authenticate": f"Bearer error={_SECRET}"}),
        ],
    )
    def test_server_text_is_not_quoted(self, answer: A2AHttpResponse) -> None:
        messages = [
            message_success_problem(answer),
            rpc_error_problem(answer, -1),
            CONSTRAINT_VIOLATION_403.problem(answer),
        ]

        assert all(message is None or _SECRET not in message for message in messages)
        assert any(message is not None for message in messages)
