"""Unit tests for request/response correlation binding.

Unary receive and streaming share ``asap.transport.errors``. These tests lock
the split: response payloads bind in both paths, ``TaskStream`` binds only on
the stream path, and acks stay unbound.
"""

from __future__ import annotations

import pytest

from typing import Any

from asap.models.envelope import Envelope
from asap.transport.errors import (
    ProtocolCorrelationError,
    assert_correlation_binds,
    assert_stream_correlation_binds,
)

_REQUEST_ID = "req-envelope-1"
_OTHER_ID = "req-envelope-2"


def _envelope(
    payload_type: str,
    payload: dict[str, Any],
    correlation_id: str | None,
) -> Envelope:
    """Build an envelope, skipping model validation when ``correlation_id`` is omitted."""
    common: dict[str, Any] = {
        "asap_version": "0.1",
        "sender": "urn:asap:agent:server",
        "recipient": "urn:asap:agent:client",
        "payload_type": payload_type,
        "payload": payload,
    }
    if correlation_id is None:
        # Envelope rejects a missing correlation_id on bound types. The helper
        # is the backstop if that shape is assembled without the model validator.
        return Envelope.model_construct(**common, correlation_id=None)
    return Envelope(**common, correlation_id=correlation_id)


def _task_response() -> dict[str, Any]:
    return {"task_id": "task_1", "status": "completed", "result": {"ok": True}}


class TestCorrelationBinding:
    """Binding contract shared by the HTTP client and WebSocket transport."""

    def test_matching_task_response_binds_on_both_paths(self) -> None:
        response = _envelope("task.response", _task_response(), _REQUEST_ID)
        assert_correlation_binds(_REQUEST_ID, response)
        assert_stream_correlation_binds(_REQUEST_ID, response)

    def test_mismatched_task_response_names_both_ids(self) -> None:
        response = _envelope("task.response", _task_response(), _OTHER_ID)
        with pytest.raises(ProtocolCorrelationError) as exc_info:
            assert_correlation_binds(_REQUEST_ID, response)
        error = exc_info.value
        assert error.request_id == _REQUEST_ID
        assert error.correlation_id == _OTHER_ID
        assert repr(_REQUEST_ID) in str(error)
        assert repr(_OTHER_ID) in str(error)
        with pytest.raises(ProtocolCorrelationError):
            assert_stream_correlation_binds(_REQUEST_ID, response)

    def test_dotted_payload_type_still_binds(self) -> None:
        """``Task.Response`` normalizes to the same key as ``task.response``."""
        response = _envelope("Task.Response", _task_response(), _OTHER_ID)
        with pytest.raises(ProtocolCorrelationError) as exc_info:
            assert_correlation_binds(_REQUEST_ID, response)
        assert exc_info.value.correlation_id == _OTHER_ID

    def test_message_ack_skips_binding(self) -> None:
        ack = _envelope(
            "message.ack",
            {"original_envelope_id": "env-1", "status": "received"},
            _OTHER_ID,
        )
        assert_correlation_binds(_REQUEST_ID, ack)
        assert_stream_correlation_binds(_REQUEST_ID, ack)

    def test_task_stream_binds_only_on_the_stream_path(self) -> None:
        chunk = _envelope("task.stream", {"chunk": "tok", "final": False}, _OTHER_ID)
        assert_correlation_binds(_REQUEST_ID, chunk)
        with pytest.raises(ProtocolCorrelationError) as exc_info:
            assert_stream_correlation_binds(_REQUEST_ID, chunk)
        assert exc_info.value.correlation_id == _OTHER_ID

    def test_mcp_tool_result_mismatch_rejected(self) -> None:
        result = _envelope(
            "mcp.tool.result",
            {"request_id": "call-1", "success": True, "result": {"ok": True}},
            _OTHER_ID,
        )
        with pytest.raises(ProtocolCorrelationError):
            assert_correlation_binds(_REQUEST_ID, result)

    def test_missing_correlation_id_on_bound_type_rejected(self) -> None:
        response = _envelope("task.response", _task_response(), None)
        with pytest.raises(ProtocolCorrelationError) as exc_info:
            assert_correlation_binds(_REQUEST_ID, response)
        assert exc_info.value.correlation_id is None
        assert exc_info.value.request_id == _REQUEST_ID
