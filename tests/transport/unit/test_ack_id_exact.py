"""A MessageAck clears only the pending envelope with that exact id.

The happy path pops ``env-test``. A prefix, case, or padded id would still
leave that test green while cancelling a different retransmission.
"""

from __future__ import annotations

import json

from asap.models.envelope import Envelope
from asap.models.payloads import MessageAck, TaskRequest
from asap.transport.websocket import ASAP_ACK_METHOD, PendingAck, WebSocketTransport

_EXACT = "env-01"
_NEIGHBORS: tuple[str, ...] = (
    "env-010",
    "env-0",
    "ENV-01",
    "env-01 ",
    " env-01",
)


def _pending_envelope() -> Envelope:
    """Envelope parked under the exact ack id."""
    return Envelope(
        id=_EXACT,
        asap_version="0.1",
        sender="urn:asap:agent:a",
        recipient="urn:asap:agent:b",
        payload_type="task.request",
        payload=TaskRequest(
            conversation_id="conv_01",
            skill_id="echo",
            input={"text": "hello"},
        ).model_dump(),
    )


def _ack_frame(original_envelope_id: str) -> dict[str, object]:
    """Build an asap.ack frame for ``original_envelope_id``."""
    envelope = Envelope(
        asap_version="0.1",
        sender="urn:asap:agent:b",
        recipient="urn:asap:agent:a",
        payload_type="MessageAck",
        payload=MessageAck(
            original_envelope_id=original_envelope_id,
            status="received",
        ).model_dump(),
    )
    return {
        "jsonrpc": "2.0",
        "method": ASAP_ACK_METHOD,
        "params": {"envelope": json.loads(envelope.model_dump_json())},
    }


def test_ack_pops_only_the_exact_envelope_id() -> None:
    """Neighbor ack ids leave the pending envelope in place."""
    transport = WebSocketTransport()
    transport._pending_acks[_EXACT] = PendingAck(
        envelope_id=_EXACT,
        sent_at=0.0,
        retries=0,
        original_envelope=_pending_envelope(),
    )

    for original_id in _NEIGHBORS:
        transport._consume_ack_frame(_ack_frame(original_id))
        assert _EXACT in transport._pending_acks

    transport._consume_ack_frame(_ack_frame(_EXACT))
    assert _EXACT not in transport._pending_acks
