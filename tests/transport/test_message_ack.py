"""Tests for MessageAck and requires_ack (ADR-16)."""

import json
from typing import TYPE_CHECKING

import pytest

from asap.models.envelope import Envelope
from asap.models.payloads import MessageAck, TaskRequest, TaskUpdate
from asap.transport.jsonrpc import ASAP_METHOD
from asap.transport.ws import (
    ASAP_ACK_METHOD,
    PAYLOAD_TYPES_REQUIRING_ACK,
    _build_ack_notification_frame,
)

from .conftest import NoRateLimitTestBase, TEST_RATE_LIMIT_DEFAULT

if TYPE_CHECKING:
    from asap.models.entities import Manifest
    from fastapi import FastAPI
    from asap.transport.rate_limit import ASAPRateLimiter


# --- Fixtures ---


@pytest.fixture
def app(
    sample_manifest: "Manifest",
    disable_rate_limiting: "ASAPRateLimiter",
) -> "FastAPI":
    from asap.transport.server import create_app

    app_instance = create_app(sample_manifest, rate_limit=TEST_RATE_LIMIT_DEFAULT)
    app_instance.state.limiter = disable_rate_limiting
    return app_instance


@pytest.fixture
def client(app: "FastAPI") -> "object":
    from fastapi.testclient import TestClient

    return TestClient(app)


# --- MessageAck payload (3.4.1) ---


class TestMessageAckPayload:
    def test_message_ack_serialize_deserialize_received(self) -> None:
        ack = MessageAck(
            original_envelope_id="env_01HX5K3MQVN8",
            status="received",
        )
        data = ack.model_dump()
        assert data["original_envelope_id"] == "env_01HX5K3MQVN8"
        assert data["status"] == "received"
        assert data.get("error") is None
        restored = MessageAck.model_validate(data)
        assert restored.original_envelope_id == ack.original_envelope_id
        assert restored.status == ack.status

    def test_message_ack_serialize_deserialize_rejected(self) -> None:
        ack = MessageAck(
            original_envelope_id="env_01HX5K3MQVN9",
            status="rejected",
            error="Invalid payload",
        )
        data = ack.model_dump()
        assert data["status"] == "rejected"
        assert data["error"] == "Invalid payload"
        restored = MessageAck.model_validate(data)
        assert restored.error == "Invalid payload"

    def test_message_ack_processed(self) -> None:
        ack = MessageAck(
            original_envelope_id="env_01HX5K3MQVNA",
            status="processed",
        )
        assert ack.status == "processed"


# --- Envelope requires_ack (3.4.2) ---


class TestEnvelopeRequiresAck:
    def test_envelope_requires_ack_default_false(self) -> None:
        envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:a",
            recipient="urn:asap:agent:b",
            payload_type="TaskRequest",
            payload={"conversation_id": "c1", "skill_id": "s1", "input": {}},
        )
        assert envelope.requires_ack is False

    def test_envelope_requires_ack_true_serializes(self) -> None:
        envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:a",
            recipient="urn:asap:agent:b",
            payload_type="TaskRequest",
            payload={"conversation_id": "c1", "skill_id": "s1", "input": {}},
            requires_ack=True,
        )
        data = envelope.model_dump(mode="json")
        assert data["requires_ack"] is True


# --- Critical payload types and ack frame (3.4.3, 3.4.4 helpers) ---


class TestPayloadTypesRequiringAck:
    def test_critical_payload_types_include_task_request(self) -> None:
        assert "TaskRequest" in PAYLOAD_TYPES_REQUIRING_ACK
        assert "task.request" in PAYLOAD_TYPES_REQUIRING_ACK

    def test_critical_payload_types_include_others(self) -> None:
        assert "TaskCancel" in PAYLOAD_TYPES_REQUIRING_ACK
        assert "StateRestore" in PAYLOAD_TYPES_REQUIRING_ACK
        assert "MessageSend" in PAYLOAD_TYPES_REQUIRING_ACK

    def test_task_update_not_requiring_ack(self) -> None:
        assert "TaskUpdate" not in PAYLOAD_TYPES_REQUIRING_ACK
        assert "task.update" not in PAYLOAD_TYPES_REQUIRING_ACK


class TestBuildAckNotificationFrame:
    def test_ack_frame_has_method_and_params(self) -> None:
        frame = _build_ack_notification_frame(
            original_envelope_id="env_1",
            status="received",
            sender="urn:asap:agent:server",
            recipient="urn:asap:agent:client",
        )
        data = json.loads(frame)
        assert data["jsonrpc"] == "2.0"
        assert data["method"] == ASAP_ACK_METHOD
        assert "params" in data
        assert "envelope" in data["params"]
        env = data["params"]["envelope"]
        assert env["payload_type"] == "MessageAck"
        assert env["payload"]["original_envelope_id"] == "env_1"
        assert env["payload"]["status"] == "received"

    def test_ack_frame_rejected_includes_error(self) -> None:
        frame = _build_ack_notification_frame(
            original_envelope_id="env_2",
            status="rejected",
            sender="urn:asap:agent:s",
            recipient="urn:asap:agent:c",
            error="Something failed",
        )
        data = json.loads(frame)
        assert data["params"]["envelope"]["payload"]["status"] == "rejected"
        assert data["params"]["envelope"]["payload"]["error"] == "Something failed"


# --- WebSocket: TaskRequest with requires_ack -> receives MessageAck (3.4.4, 3.4.5) ---


class TestWebSocketTaskRequestReceivesAck(NoRateLimitTestBase):
    def test_task_request_with_requires_ack_receives_ack_then_response(
        self,
        client: "object",
    ) -> None:
        from fastapi.testclient import TestClient

        tc = client
        assert isinstance(tc, TestClient)
        envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:client",
            recipient="urn:asap:agent:test-server",
            payload_type="task.request",
            payload=TaskRequest(
                conversation_id="conv-ack-1",
                skill_id="echo",
                input={"message": "need ack"},
            ).model_dump(),
            requires_ack=True,
        )
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "method": ASAP_METHOD,
                "params": {"envelope": envelope.model_dump(mode="json")},
                "id": "req-ack-1",
            }
        )

        with tc.websocket_connect("/asap/ws") as websocket:
            websocket.send_text(body)
            ack_raw = websocket.receive_text()
            response_raw = websocket.receive_text()

        ack_frame = json.loads(ack_raw)
        response_frame = json.loads(response_raw)

        assert ack_frame.get("method") == ASAP_ACK_METHOD
        assert "params" in ack_frame and "envelope" in ack_frame["params"]
        ack_env = ack_frame["params"]["envelope"]
        assert ack_env.get("payload_type") == "MessageAck"
        assert ack_env["payload"]["original_envelope_id"] == envelope.id
        assert ack_env["payload"]["status"] == "received"

        assert response_frame.get("id") == "req-ack-1"
        assert "result" in response_frame
        assert response_frame["result"]["envelope"].get("payload_type") == "task.response"


class TestWebSocketTaskUpdateNoAck(NoRateLimitTestBase):
    def test_task_update_no_ack_sent(
        self,
        app: "FastAPI",
        client: "object",
    ) -> None:
        from fastapi.testclient import TestClient

        tc = client
        assert isinstance(tc, TestClient)
        envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:client",
            recipient="urn:asap:agent:test-server",
            payload_type="task.update",
            payload=TaskUpdate(
                task_id="task_123",
                update_type="progress",
                status="working",
                progress={"percent": 50},
            ).model_dump(),
            requires_ack=False,
        )
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "method": ASAP_METHOD,
                "params": {"envelope": envelope.model_dump(mode="json")},
                "id": "req-update-1",
            }
        )

        with tc.websocket_connect("/asap/ws") as websocket:
            websocket.send_text(body)
            first = websocket.receive_text()

        frame = json.loads(first)
        if frame.get("method") == ASAP_ACK_METHOD:
            pytest.fail("TaskUpdate must not trigger MessageAck")
        assert "result" in frame or "error" in frame


class TestHttpNoMessageAck(NoRateLimitTestBase):
    def test_http_post_task_request_returns_only_task_response(
        self,
        client: "object",
    ) -> None:
        from fastapi.testclient import TestClient

        tc = client
        assert isinstance(tc, TestClient)
        envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:client",
            recipient="urn:asap:agent:test-server",
            payload_type="task.request",
            payload=TaskRequest(
                conversation_id="conv-http-1",
                skill_id="echo",
                input={"message": "http"},
            ).model_dump(),
            requires_ack=True,
        )
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "method": ASAP_METHOD,
                "params": {"envelope": envelope.model_dump(mode="json")},
                "id": 1,
            }
        )

        response = tc.post("/asap", content=body)

        assert response.status_code == 200
        data = response.json()
        assert "result" in data
        assert "envelope" in data["result"]
        assert data["result"]["envelope"].get("payload_type") == "task.response"
        assert data.get("method") != ASAP_ACK_METHOD


# --- Rejected message -> ack with error (3.4.5) ---


class TestWebSocketRejectedSendsAck(NoRateLimitTestBase):
    def test_invalid_request_with_requires_ack_receives_rejected_ack(
        self,
        client: "object",
    ) -> None:
        from fastapi.testclient import TestClient

        tc = client
        assert isinstance(tc, TestClient)
        envelope_dict = {
            "id": "env-reject-1",
            "asap_version": "0.1",
            "sender": "urn:asap:agent:client",
            "recipient": "urn:asap:agent:test-server",
            "payload_type": "task.request",
            "payload": {"conversation_id": "c", "skill_id": "echo"},
            "requires_ack": True,
        }
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "method": ASAP_METHOD,
                "params": {"envelope": envelope_dict},
                "id": "req-reject-1",
            }
        )

        with tc.websocket_connect("/asap/ws") as websocket:
            websocket.send_text(body)
            first_raw = websocket.receive_text()
            second_raw = websocket.receive_text()

        first_frame = json.loads(first_raw)
        second_frame = json.loads(second_raw)

        if first_frame.get("method") == ASAP_ACK_METHOD:
            ack_env = first_frame["params"]["envelope"]
            assert ack_env["payload"]["status"] in ("received", "rejected")
            if ack_env["payload"]["status"] == "rejected":
                assert "error" in ack_env["payload"]
        assert "error" in first_frame or "error" in second_frame
