import asyncio
import json

import pytest

from asap.models.envelope import Envelope
from asap.models.payloads import TaskRequest
from asap.transport.circuit_breaker import CircuitState, get_registry
from asap.transport.ws import (
    ACK_CHECK_INTERVAL,
    DEFAULT_ACK_TIMEOUT,
    DEFAULT_MAX_ACK_RETRIES,
    PAYLOAD_TYPES_REQUIRING_ACK,
    PendingAck,
    WebSocketTransport,
)
import contextlib


# --- Fixtures ---


@pytest.fixture(autouse=True)
def clear_circuit_breaker_registry() -> None:
    get_registry().clear()
    yield
    get_registry().clear()


# --- Pending ack tracker (3.5.1) ---


class TestPendingAckTracker:
    def test_normal_flow_register_then_clear_pending_ack(self) -> None:
        transport = WebSocketTransport(
            receive_timeout=5.0,
            ack_timeout_seconds=30.0,
        )
        envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:a",
            recipient="urn:asap:agent:b",
            payload_type="TaskRequest",
            payload=TaskRequest(
                conversation_id="c1",
                skill_id="s1",
                input={},
            ).model_dump(),
        )
        assert envelope.id is not None
        transport._register_pending_ack(envelope)
        assert envelope.id in transport._pending_acks
        transport._pending_acks.pop(envelope.id, None)
        assert envelope.id not in transport._pending_acks

    def test_pending_ack_dataclass_fields(self) -> None:
        envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:a",
            recipient="urn:asap:agent:b",
            payload_type="TaskRequest",
            payload=TaskRequest(conversation_id="c1", skill_id="s1", input={}).model_dump(),
        )
        pending = PendingAck(
            envelope_id=envelope.id or "id",
            sent_at=0.0,
            retries=1,
            original_envelope=envelope,
        )
        assert pending.envelope_id == envelope.id
        assert pending.retries == 1
        assert pending.original_envelope is envelope

    def test_requires_ack_true_for_task_request(self) -> None:
        transport = WebSocketTransport()
        envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:a",
            recipient="urn:asap:agent:b",
            payload_type="TaskRequest",
            payload=TaskRequest(conversation_id="c1", skill_id="s1", input={}).model_dump(),
        )
        assert transport._requires_ack(envelope) is True

    def test_requires_ack_false_for_task_update(self) -> None:
        transport = WebSocketTransport()
        envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:a",
            recipient="urn:asap:agent:b",
            payload_type="TaskUpdate",
            payload={
                "task_id": "t1",
                "update_type": "progress",
                "status": "working",
                "progress": {"percent": 50},
            },
        )
        assert transport._requires_ack(envelope) is False

    def test_register_pending_ack_skips_non_critical(self) -> None:
        transport = WebSocketTransport()
        envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:a",
            recipient="urn:asap:agent:b",
            payload_type="TaskUpdate",
            payload={
                "task_id": "t1",
                "update_type": "progress",
                "status": "working",
                "progress": {},
            },
        )
        transport._register_pending_ack(envelope)
        assert envelope.id not in transport._pending_acks

    def test_register_pending_ack_adds_critical_message(self) -> None:
        transport = WebSocketTransport()
        envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:a",
            recipient="urn:asap:agent:b",
            payload_type="TaskRequest",
            payload=TaskRequest(conversation_id="c1", skill_id="s1", input={}).model_dump(),
        )
        transport._register_pending_ack(envelope)
        assert envelope.id in transport._pending_acks

    def test_concurrent_messages_multiple_pending_tracked_independently(self) -> None:
        transport = WebSocketTransport()
        e1 = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:a",
            recipient="urn:asap:agent:b",
            payload_type="TaskRequest",
            payload=TaskRequest(conversation_id="c1", skill_id="s1", input={}).model_dump(),
        )
        e2 = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:a",
            recipient="urn:asap:agent:b",
            payload_type="TaskRequest",
            payload=TaskRequest(conversation_id="c2", skill_id="s2", input={}).model_dump(),
        )
        transport._register_pending_ack(e1)
        transport._register_pending_ack(e2)
        assert e1.id in transport._pending_acks
        assert e2.id in transport._pending_acks
        transport._pending_acks.pop(e1.id, None)
        assert e1.id not in transport._pending_acks
        assert e2.id in transport._pending_acks


# --- Idempotency (3.5.3) ---


class TestRetransmitIdempotency:
    def test_pending_ack_stores_original_envelope(self) -> None:
        envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:a",
            recipient="urn:asap:agent:b",
            payload_type="TaskRequest",
            payload=TaskRequest(conversation_id="c1", skill_id="s1", input={}).model_dump(),
        )
        eid = envelope.id
        pending = PendingAck(
            envelope_id=eid or "",
            sent_at=0.0,
            retries=0,
            original_envelope=envelope,
        )
        assert pending.original_envelope.id == eid
        # Retransmitting pending.original_envelope sends same id
        assert pending.original_envelope.model_dump(mode="json").get("id") == eid


# --- Timeout and retransmission (3.5.2, 3.5.3) ---


class TestAckTimeoutAndRetransmit:
    @pytest.mark.asyncio
    async def test_ack_check_loop_retransmits_on_timeout(self) -> None:
        sent_frames: list[str] = []
        original_envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:a",
            recipient="urn:asap:agent:b",
            payload_type="TaskRequest",
            payload=TaskRequest(conversation_id="c1", skill_id="s1", input={}).model_dump(),
        )
        envelope_id = original_envelope.id
        assert envelope_id is not None

        class FakeWs:
            async def send(self, data: str) -> None:
                sent_frames.append(data)

            async def recv(self) -> str:
                await asyncio.sleep(999)

        transport = WebSocketTransport(
            receive_timeout=2.0,
            ack_timeout_seconds=0.05,
            max_ack_retries=3,
            ack_check_interval=0.02,
        )
        transport._ws = FakeWs()
        transport._recv_task = asyncio.create_task(transport._recv_loop())
        transport._ack_check_task = asyncio.create_task(transport._ack_check_loop())
        transport._register_pending_ack(original_envelope)
        try:
            await asyncio.sleep(0.15)
        finally:
            transport._closed = True
            if transport._ack_check_task:
                transport._ack_check_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await transport._ack_check_task
            if transport._recv_task:
                transport._recv_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await transport._recv_task
        assert len(sent_frames) >= 1
        for frame in sent_frames:
            data = json.loads(frame)
            params = data.get("params", {})
            env = params.get("envelope", {})
            assert env.get("id") == envelope_id

    @pytest.mark.asyncio
    async def test_max_retries_records_circuit_breaker_failure(self) -> None:
        registry = get_registry()
        breaker = registry.get_or_create("http://test.example/", threshold=1, timeout=60.0)
        assert breaker.get_state() == CircuitState.CLOSED

        class FakeWs:
            async def send(self, data: str) -> None:
                pass

            async def recv(self) -> str:
                await asyncio.sleep(999)

        transport = WebSocketTransport(
            receive_timeout=2.0,
            ack_timeout_seconds=0.05,
            max_ack_retries=2,
            ack_check_interval=0.02,
            circuit_breaker=breaker,
        )
        transport._ws = FakeWs()
        transport._recv_task = asyncio.create_task(transport._recv_loop())
        transport._ack_check_task = asyncio.create_task(transport._ack_check_loop())
        envelope = Envelope(
            asap_version="0.1",
            sender="urn:asap:agent:a",
            recipient="urn:asap:agent:b",
            payload_type="TaskRequest",
            payload=TaskRequest(conversation_id="c1", skill_id="s1", input={}).model_dump(),
        )
        transport._register_pending_ack(envelope)
        try:
            # Wait until max-retry path removes the pending ack (needs one full
            # ack_timeout after the last retransmit); avoid racing cancellation.
            for _ in range(100):
                if envelope.id not in transport._pending_acks:
                    break
                await asyncio.sleep(0.02)
            else:
                pytest.fail("pending ack was not removed after max retries")
        finally:
            transport._closed = True
            if transport._ack_check_task:
                transport._ack_check_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await transport._ack_check_task
            if transport._recv_task:
                transport._recv_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await transport._recv_task
        assert envelope.id not in transport._pending_acks
        assert breaker.get_state() == CircuitState.OPEN
        assert breaker.get_consecutive_failures() >= 1


# --- Circuit breaker integration (3.5.4) ---


class TestCircuitBreakerIntegration:
    def test_transport_accepts_optional_circuit_breaker(self) -> None:
        t1 = WebSocketTransport(circuit_breaker=None)
        assert t1._circuit_breaker is None
        registry = get_registry()
        breaker = registry.get_or_create("http://example/", threshold=2, timeout=10.0)
        t2 = WebSocketTransport(circuit_breaker=breaker)
        assert t2._circuit_breaker is breaker


# --- Constants ---


class TestAckAwareConstants:
    def test_ack_check_interval_defined(self) -> None:
        assert ACK_CHECK_INTERVAL == 5.0

    def test_default_ack_timeout_defined(self) -> None:
        assert DEFAULT_ACK_TIMEOUT == 30.0

    def test_default_max_ack_retries_defined(self) -> None:
        assert DEFAULT_MAX_ACK_RETRIES == 3

    def test_payload_types_requiring_ack_include_task_request(self) -> None:
        assert "TaskRequest" in PAYLOAD_TYPES_REQUIRING_ACK
        assert "task.request" in PAYLOAD_TYPES_REQUIRING_ACK
