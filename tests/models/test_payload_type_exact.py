"""Payload-type lookup is exact after normalization, not a prefix match.

``task.request`` and ``TaskRequest`` already parse as ``TaskRequest``. A
neighbor that only contains that name must stay an untyped dict, and a
neighbor of ``task.response`` must not start requiring ``correlation_id``.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from asap.models.envelope import Envelope
from asap.models.payloads import TaskRequest, TaskResponse

_REQUEST_BODY: dict[str, Any] = {
    "conversation_id": "conv_exact",
    "skill_id": "echo",
    "input": {},
}
# Missing skill_id. A registry hit raises. A miss stays a dict, because a
# complete TaskRequest body is coerced by the payload union even on a miss.
_INCOMPLETE_REQUEST: dict[str, Any] = {
    "conversation_id": "conv_exact",
    "input": {},
}
_RESPONSE_BODY: dict[str, Any] = {"task_id": "task_01", "status": "completed"}

# Normalization lowercases and drops non-alphanumeric characters.
_TASK_REQUEST_ALIASES: tuple[str, ...] = (
    "task.request",
    "task_request",
    "TASK.REQUEST",
    " task.request ",
)
# These stay distinct after that normalization.
_TASK_REQUEST_NEIGHBORS: tuple[str, ...] = (
    "task.request.admin",
    "taskrequestextra",
    "task.reques",
    "task.requests",
)
_CORRELATION_ALIASES: tuple[str, ...] = (
    "task.response",
    "task_response",
    "TASK.RESPONSE",
    " task.response ",
)
_CORRELATION_NEIGHBORS: tuple[str, ...] = (
    "task.response.admin",
    "taskresponseextra",
    "task.respons",
    "task.responses",
)


def _envelope(
    payload_type: str,
    payload: dict[str, Any],
    correlation_id: str | None = None,
) -> Envelope:
    """Build one envelope. ``correlation_id`` is omitted when None."""
    return Envelope(
        asap_version="0.1",
        sender="urn:asap:agent:client",
        recipient="urn:asap:agent:server",
        payload_type=payload_type,
        payload=payload,
        correlation_id=correlation_id,
    )


def test_task_request_lookup_is_exact_after_normalization() -> None:
    """Aliases validate as TaskRequest. Neighbors keep an incomplete body as a dict."""
    for payload_type in _TASK_REQUEST_ALIASES:
        parsed = _envelope(payload_type, _REQUEST_BODY)
        assert isinstance(parsed.payload, TaskRequest)
        assert parsed.payload_type == payload_type
        with pytest.raises(ValidationError, match="skill_id"):
            _envelope(payload_type, _INCOMPLETE_REQUEST)

    for payload_type in _TASK_REQUEST_NEIGHBORS:
        envelope = _envelope(payload_type, _INCOMPLETE_REQUEST)
        assert isinstance(envelope.payload, dict), payload_type
        assert envelope.payload == _INCOMPLETE_REQUEST


def test_correlation_requirement_follows_the_normalized_name() -> None:
    """Dotted and padded response names require correlation_id. Neighbors do not."""
    for payload_type in _CORRELATION_ALIASES:
        with pytest.raises(ValidationError, match="must have correlation_id"):
            _envelope(payload_type, _RESPONSE_BODY)
        paired = _envelope(payload_type, _RESPONSE_BODY, correlation_id="req-1")
        assert isinstance(paired.payload, TaskResponse)
        assert paired.correlation_id == "req-1"

    for payload_type in _CORRELATION_NEIGHBORS:
        unpaired = _envelope(payload_type, _RESPONSE_BODY)
        assert unpaired.correlation_id is None
        assert isinstance(unpaired.payload, dict)
