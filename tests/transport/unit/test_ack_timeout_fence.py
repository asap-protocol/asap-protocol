"""Lock ADR-16 ack retransmit and expiry at the exact timeout instant.

``_retransmit_or_expire_pending_acks`` skips a pending ack while
``now - sent_at <= timeout``. The next monotonic instant retransmits, or
expires the ack once the retry budget is spent.
"""

from __future__ import annotations

import math
from unittest.mock import AsyncMock, Mock

import pytest

from asap.models.envelope import Envelope
from asap.transport.websocket import WebSocketTransport

_SENT_AT = 100.0
_TIMEOUT = 10.0


def _envelope() -> Envelope:
    return Envelope(
        asap_version="0.1",
        sender="urn:asap:agent:a",
        recipient="urn:asap:agent:b",
        payload_type="TaskRequest",
        payload={"conversation_id": "c1", "skill_id": "s1", "input": {}},
        id="env-ack-fence",
        requires_ack=True,
    )


def _transport_at(
    monkeypatch: pytest.MonkeyPatch,
    *,
    retries: int,
) -> tuple[WebSocketTransport, AsyncMock, Mock, list[float]]:
    clock = [_SENT_AT + _TIMEOUT]
    monkeypatch.setattr("asap.transport.ws._ack.time.monotonic", lambda: clock[0])
    breaker = Mock()
    transport = WebSocketTransport(
        ack_timeout_seconds=_TIMEOUT,
        max_ack_retries=3,
        circuit_breaker=breaker,
    )
    send = AsyncMock()
    transport._send_envelope_only = send
    transport._register_pending_ack(_envelope())
    pending = transport._pending_acks["env-ack-fence"]
    pending.sent_at = _SENT_AT
    pending.retries = retries
    return transport, send, breaker, clock


async def test_ack_does_not_retransmit_until_timeout_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Elapsed time equal to the timeout keeps the ack; the next instant sends once."""
    transport, send, breaker, clock = _transport_at(monkeypatch, retries=0)

    await transport._retransmit_or_expire_pending_acks()
    pending = transport._pending_acks["env-ack-fence"]
    assert send.await_count == 0
    assert pending.retries == 0
    assert pending.sent_at == _SENT_AT
    breaker.record_failure.assert_not_called()

    clock[0] = math.nextafter(_SENT_AT + _TIMEOUT, math.inf)
    await transport._retransmit_or_expire_pending_acks()

    pending = transport._pending_acks["env-ack-fence"]
    assert send.await_count == 1
    sent = send.await_args
    assert sent is not None
    assert sent.args[0].id == "env-ack-fence"
    assert pending.retries == 1
    assert pending.sent_at == clock[0]
    breaker.record_failure.assert_not_called()


async def test_ack_expires_only_after_timeout_when_retries_are_spent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A spent retry budget still waits out the timeout before recording failure."""
    transport, send, breaker, clock = _transport_at(monkeypatch, retries=3)

    await transport._retransmit_or_expire_pending_acks()
    assert "env-ack-fence" in transport._pending_acks
    assert send.await_count == 0
    breaker.record_failure.assert_not_called()

    clock[0] = math.nextafter(_SENT_AT + _TIMEOUT, math.inf)
    await transport._retransmit_or_expire_pending_acks()

    assert "env-ack-fence" not in transport._pending_acks
    assert send.await_count == 0
    breaker.record_failure.assert_called_once()
