"""Tests for Envelope model (message wrapper)."""

from datetime import datetime, timezone
from typing import Any

import pytest
from pydantic import ValidationError


class TestEnvelope:
    """Test suite for Envelope model."""

    def test_envelope_creation_with_all_fields(self):
        """Test creating an Envelope with all fields explicitly provided."""
        from asap.models.envelope import Envelope
        from asap.models.ids import generate_id

        env_id = generate_id()
        timestamp = datetime.now(timezone.utc)

        envelope = Envelope(
            id=env_id,
            asap_version="0.1",
            timestamp=timestamp,
            sender="urn:asap:agent:coordinator",
            recipient="urn:asap:agent:research-v1",
            payload_type="TaskRequest",
            payload={"conversation_id": "conv_123", "skill_id": "research", "input": {}},
        )

        assert envelope.id == env_id
        assert envelope.asap_version == "0.1"
        assert envelope.timestamp == timestamp
        assert envelope.sender == "urn:asap:agent:coordinator"
        assert envelope.recipient == "urn:asap:agent:research-v1"
        assert envelope.payload_type == "TaskRequest"
        assert envelope.payload.skill_id == "research"

    def test_envelope_auto_generates_id(self):
        """Test that Envelope auto-generates id if not provided."""
        from asap.models.envelope import Envelope
        from datetime import datetime, timezone

        envelope = Envelope(
            asap_version="0.1",
            timestamp=datetime.now(timezone.utc),
            sender="urn:asap:agent:a",
            recipient="urn:asap:agent:b",
            payload_type="TaskRequest",
            payload={"conversation_id": "c1", "skill_id": "s1", "input": {}},
        )

        assert envelope.id is not None
        assert isinstance(envelope.id, str)
        assert len(envelope.id) > 0

    def test_envelope_auto_generates_timestamp(self):
        """Test that Envelope auto-generates timestamp if not provided."""
        from asap.models.envelope import Envelope
        from asap.models.ids import generate_id

        before = datetime.now(timezone.utc)
        envelope = Envelope(
            id=generate_id(),
            asap_version="0.1",
            sender="urn:asap:agent:a",
            recipient="urn:asap:agent:b",
            payload_type="TaskRequest",
            payload={"conversation_id": "c1", "skill_id": "s1", "input": {}},
        )
        after = datetime.now(timezone.utc)

        assert envelope.timestamp is not None
        assert isinstance(envelope.timestamp, datetime)
        assert before <= envelope.timestamp <= after

    def test_envelope_with_correlation_id(self):
        """Test Envelope with correlation_id for request tracking."""
        from asap.models.envelope import Envelope
        from datetime import datetime, timezone

        envelope = Envelope(
            asap_version="0.1",
            timestamp=datetime.now(timezone.utc),
            sender="urn:asap:agent:a",
            recipient="urn:asap:agent:b",
            payload_type="TaskResponse",
            payload={"task_id": "task_123", "status": "completed"},
            correlation_id="req_external_123",
        )

        assert envelope.correlation_id == "req_external_123"

    def test_envelope_with_trace_id(self):
        """Test Envelope with trace_id for distributed tracing."""
        from asap.models.envelope import Envelope
        from datetime import datetime, timezone

        envelope = Envelope(
            asap_version="0.1",
            timestamp=datetime.now(timezone.utc),
            sender="urn:asap:agent:a",
            recipient="urn:asap:agent:b",
            payload_type="TaskRequest",
            payload={"conversation_id": "c1", "skill_id": "s1", "input": {}},
            trace_id="trace_01HX5K...",
        )

        assert envelope.trace_id == "trace_01HX5K..."

    def test_envelope_with_extensions(self):
        """Test Envelope with extensions field."""
        from asap.models.envelope import Envelope
        from datetime import datetime, timezone

        extensions = {"priority": "high", "custom_metadata": {"source": "api"}}

        envelope = Envelope(
            asap_version="0.1",
            timestamp=datetime.now(timezone.utc),
            sender="urn:asap:agent:a",
            recipient="urn:asap:agent:b",
            payload_type="TaskRequest",
            payload={"conversation_id": "c1", "skill_id": "s1", "input": {}},
            extensions=extensions,
        )

        assert envelope.extensions is not None
        assert envelope.extensions["priority"] == "high"
        assert envelope.extensions["custom_metadata"]["source"] == "api"

    def test_envelope_required_fields(self):
        """Test that Envelope requires all mandatory fields."""
        from asap.models.envelope import Envelope

        # Missing sender
        with pytest.raises(ValidationError):
            Envelope(
                asap_version="0.1",
                recipient="urn:asap:agent:b",
                payload_type="TaskRequest",
                payload={},
            )

        # Missing recipient
        with pytest.raises(ValidationError):
            Envelope(
                asap_version="0.1",
                sender="urn:asap:agent:a",
                payload_type="TaskRequest",
                payload={},
            )

        # Missing payload_type
        with pytest.raises(ValidationError):
            Envelope(
                asap_version="0.1",
                sender="urn:asap:agent:a",
                recipient="urn:asap:agent:b",
                payload={},
            )

        # Missing payload
        with pytest.raises(ValidationError):
            Envelope(
                asap_version="0.1",
                sender="urn:asap:agent:a",
                recipient="urn:asap:agent:b",
                payload_type="TaskRequest",
            )

    def test_envelope_json_schema(self):
        """Test that Envelope generates valid JSON Schema."""
        from asap.models.envelope import Envelope

        schema = Envelope.model_json_schema()

        assert schema["type"] == "object"
        assert "id" in schema["properties"]
        assert "asap_version" in schema["properties"]
        assert "timestamp" in schema["properties"]
        assert "sender" in schema["properties"]
        assert "recipient" in schema["properties"]
        assert "payload_type" in schema["properties"]
        assert "payload" in schema["properties"]

        required = set(schema["required"])
        assert "asap_version" in required
        assert "sender" in required
        assert "recipient" in required
        assert "payload_type" in required
        assert "payload" in required

    def test_envelope_serialization(self):
        """Test Envelope serialization to dict."""
        from asap.models.envelope import Envelope
        from datetime import datetime, timezone

        envelope = Envelope(
            asap_version="0.1",
            timestamp=datetime.now(timezone.utc),
            sender="urn:asap:agent:a",
            recipient="urn:asap:agent:b",
            payload_type="TaskRequest",
            payload={"conversation_id": "conv_1", "skill_id": "s1", "input": {"test": "data"}},
        )

        data = envelope.model_dump()

        assert data["asap_version"] == "0.1"
        assert data["sender"] == "urn:asap:agent:a"
        assert data["recipient"] == "urn:asap:agent:b"
        assert data["payload_type"] == "TaskRequest"
        assert data["payload"]["input"]["test"] == "data"

    def test_response_payload_requires_correlation_id(self) -> None:
        """Test that response payloads must have correlation_id."""
        from asap.models.envelope import Envelope

        # TaskResponse without correlation_id fails validation
        with pytest.raises(ValidationError) as exc_info:
            Envelope(
                asap_version="0.1",
                sender="urn:asap:agent:a",
                recipient="urn:asap:agent:b",
                payload_type="TaskResponse",
                payload={"task_id": "t1", "status": "completed", "result": {"ok": True}},
                # correlation_id is missing - should fail
            )

        error_detail = exc_info.value.errors()[0]
        assert "must have correlation_id" in error_detail["msg"]

        # McpToolResult without correlation_id should fail
        with pytest.raises(ValidationError) as exc_info:
            Envelope(
                asap_version="0.1",
                sender="urn:asap:agent:a",
                recipient="urn:asap:agent:b",
                payload_type="McpToolResult",
                payload={"request_id": "r1", "success": True, "result": {"ok": True}},
                # correlation_id is missing - should fail
            )

        error_detail = exc_info.value.errors()[0]
        assert "must have correlation_id" in error_detail["msg"]

        # Valid response with correlation_id should work
        envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:a",
            recipient="urn:asap:agent:b",
            payload_type="TaskResponse",
            payload={"task_id": "t1", "status": "completed", "result": {"ok": True}},
            correlation_id="req_123",
        )
        assert envelope.correlation_id == "req_123"

    @pytest.mark.parametrize(
        ("payload_type", "payload"),
        [
            ("McpResourceData", {"resource_uri": "file://x", "content": {"text": "hi"}}),
            ("TaskStream", {"chunk": "token", "final": False}),
        ],
    )
    def test_resource_and_stream_payloads_require_correlation_id(
        self,
        payload_type: str,
        payload: dict[str, Any],
    ) -> None:
        """MCP resource data and stream chunks must stay pairable with the request."""
        from asap.models.envelope import Envelope

        with pytest.raises(ValidationError) as exc_info:
            Envelope(
                asap_version="0.1",
                sender="urn:asap:agent:a",
                recipient="urn:asap:agent:b",
                payload_type=payload_type,
                payload=payload,
            )
        assert "must have correlation_id" in exc_info.value.errors()[0]["msg"]

        envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:a",
            recipient="urn:asap:agent:b",
            payload_type=payload_type,
            payload=payload,
            correlation_id="req_123",
        )
        assert envelope.correlation_id == "req_123"

    def test_unknown_payload_type_keeps_raw_dict(self) -> None:
        """Unregistered payload types stay forward-compatible dicts."""
        from asap.models.envelope import Envelope

        raw = {"foo": 1, "bar": "baz"}
        envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:a",
            recipient="urn:asap:agent:b",
            payload_type="vendor.custom.event",
            payload=raw,
        )
        assert envelope.payload == raw
        assert envelope.payload_dict == raw
        assert isinstance(envelope.payload, dict)

    def test_malformed_task_request_raises_error(self) -> None:
        """Missing required TaskRequest fields raises ValidationError."""
        from asap.models.envelope import Envelope

        data = {
            "id": "env_1",
            "asap_version": "1.0",
            "sender": "urn:asap:agent:a",
            "recipient": "urn:asap:agent:b",
            "payload_type": "task.request",
            "payload": {"conversation_id": "conv_1", "input": {}},
        }
        with pytest.raises(ValidationError):
            Envelope.model_validate(data)
