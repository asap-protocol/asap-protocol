"""Frame codecs and constants for ASAP WebSocket JSON-RPC 2.0 text frames.

One WebSocket text frame carries one JSON-RPC 2.0 request, response, or
notification. This module defines the framing primitives shared by the WS client
and server:

- :func:`encode_envelope_frame` / :func:`decode_frame_to_json` — JSON-RPC wire framing.
- :func:`_build_ack_notification_frame` — server-side ``asap.ack`` push (ADR-16).
- :func:`_is_heartbeat_pong` — application-level heartbeat discrimination.
- The frame, heartbeat, close, SLA, and ack constants used across the WS package.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Literal, cast

from asap.models.envelope import Envelope
from asap.models.payloads import MessageAck
from asap.transport.jsonrpc import ASAP_METHOD

if TYPE_CHECKING:
    pass

# JSON-RPC method for server push of MessageAck (ADR-16).
ASAP_ACK_METHOD: Literal["asap.ack"] = "asap.ack"

# Frame encoding is JSON text only.
FRAME_ENCODING_JSON: Literal["json"] = "json"

# Default timeout for WebSocket receive (seconds).
DEFAULT_WS_RECEIVE_TIMEOUT: float = 60.0

# Heartbeat: server pings every N seconds; connections go stale after M seconds.
HEARTBEAT_PING_INTERVAL: float = 30.0
STALE_CONNECTION_TIMEOUT: float = 90.0

# Application-level ping/pong frame type (server-initiated heartbeat).
HEARTBEAT_FRAME_TYPE_PING: Literal["ping"] = "ping"
HEARTBEAT_FRAME_TYPE_PONG: Literal["pong"] = "pong"

# Reconnection backoff (1s, 2s, 4s, ...) capped at 30s.
RECONNECT_INITIAL_BACKOFF: float = 1.0
RECONNECT_MAX_BACKOFF: float = 30.0

# Connection pool sizing.
DEFAULT_POOL_MAX_SIZE: int = 10
DEFAULT_POOL_IDLE_TIMEOUT: float = 60.0

# Payload types that require MessageAck over WebSocket (ADR-16).
PAYLOAD_TYPES_REQUIRING_ACK: frozenset[str] = frozenset(
    {
        "TaskRequest",
        "task.request",
        "TaskCancel",
        "task.cancel",
        "StateRestore",
        "state_restore",
        "MessageSend",
        "message.send",
        "message_send",
    }
)

# Ack-aware client (ADR-16): poll pending acks, timeout, retry budget.
ACK_CHECK_INTERVAL: float = 5.0
DEFAULT_ACK_TIMEOUT: float = 30.0
DEFAULT_MAX_ACK_RETRIES: int = 3

# Close codes (RFC 6455). 1001 graceful shutdown, 1008 policy violation.
# 4401 is a custom application code so WS auth failures stay distinct from
# rate-limit (1008) and shutdown (1001) — see BUG #4.
WS_CLOSE_GOING_AWAY: int = 1001
WS_CLOSE_REASON_SHUTDOWN: str = "Server shutting down"
WS_CLOSE_POLICY_VIOLATION: int = 1008
WS_CLOSE_AUTH_REQUIRED: int = 4401

# JSON-RPC methods for SLA breach subscription (v1.3).
SLA_SUBSCRIBE_METHOD: str = "sla.subscribe"
SLA_UNSUBSCRIBE_METHOD: str = "sla.unsubscribe"
SLA_BREACH_NOTIFICATION_METHOD: str = "sla.breach"


def encode_envelope_frame(
    envelope: dict[str, Any],
    request_id: str | int = "",
    encoding: Literal["json"] = FRAME_ENCODING_JSON,
) -> str:
    """Serialize an ASAP envelope as a JSON-RPC 2.0 request text frame.

    Args:
        envelope: The envelope dict to carry in ``params.envelope``.
        request_id: JSON-RPC ``id`` for the request (correlated with the response).
        encoding: Frame encoding; only ``"json"`` is supported (binary path removed).

    Returns:
        A JSON string ready to send as a WebSocket text frame.

    Example:
        >>> frame = encode_envelope_frame({"sender": "a", ...}, request_id="r1")
        >>> isinstance(frame, str)
        True
    """
    del encoding  # Only JSON is supported; kept for signature compatibility.
    payload = {
        "jsonrpc": "2.0",
        "method": ASAP_METHOD,
        "params": {"envelope": envelope},
        "id": request_id,
    }
    return json.dumps(payload)


def decode_frame_to_json(raw: str) -> dict[str, Any]:
    """Parse a WebSocket text frame into its JSON-RPC dict.

    Args:
        raw: The received frame text.

    Returns:
        The parsed JSON-RPC object.

    Raises:
        ValueError: If *raw* is not valid JSON.
    """
    return cast(dict[str, Any], json.loads(raw))


def _is_heartbeat_pong(data: dict[str, Any]) -> bool:
    """True when *data* is an application-level heartbeat pong (no JSON-RPC method)."""
    return data.get("type") == HEARTBEAT_FRAME_TYPE_PONG and "method" not in data


def _build_ack_notification_frame(
    original_envelope_id: str,
    status: Literal["received", "processed", "rejected"],
    sender: str,
    recipient: str,
    asap_version: str = "0.1",
    error: str | None = None,
) -> str:
    """Build a server-push ``asap.ack`` JSON-RPC notification frame (ADR-16).

    Args:
        original_envelope_id: The envelope id being acknowledged.
        status: Ack stage — ``received``, ``processed``, or ``rejected``.
        sender: Ack envelope sender (the server's URN).
        recipient: Ack envelope recipient (the original sender).
        asap_version: ASAP wire version echoed on the ack envelope.
        error: Optional error description (set for ``rejected``).

    Returns:
        A JSON string ready to send as a WebSocket text frame.
    """
    ack_payload = MessageAck(
        original_envelope_id=original_envelope_id,
        status=status,
        error=error,
    )
    ack_envelope = Envelope(
        asap_version=asap_version,
        sender=sender,
        recipient=recipient,
        payload_type="MessageAck",
        payload=ack_payload.model_dump(),
    )
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "method": ASAP_ACK_METHOD,
            "params": {"envelope": ack_envelope.model_dump(mode="json")},
        }
    )


__all__ = [
    "ACK_CHECK_INTERVAL",
    "ASAP_ACK_METHOD",
    "DEFAULT_ACK_TIMEOUT",
    "DEFAULT_MAX_ACK_RETRIES",
    "DEFAULT_POOL_IDLE_TIMEOUT",
    "DEFAULT_POOL_MAX_SIZE",
    "DEFAULT_WS_RECEIVE_TIMEOUT",
    "FRAME_ENCODING_JSON",
    "HEARTBEAT_FRAME_TYPE_PING",
    "HEARTBEAT_FRAME_TYPE_PONG",
    "HEARTBEAT_PING_INTERVAL",
    "PAYLOAD_TYPES_REQUIRING_ACK",
    "RECONNECT_INITIAL_BACKOFF",
    "RECONNECT_MAX_BACKOFF",
    "SLA_BREACH_NOTIFICATION_METHOD",
    "SLA_SUBSCRIBE_METHOD",
    "SLA_UNSUBSCRIBE_METHOD",
    "STALE_CONNECTION_TIMEOUT",
    "WS_CLOSE_AUTH_REQUIRED",
    "WS_CLOSE_GOING_AWAY",
    "WS_CLOSE_POLICY_VIOLATION",
    "WS_CLOSE_REASON_SHUTDOWN",
    "_build_ack_notification_frame",
    "_is_heartbeat_pong",
    "decode_frame_to_json",
    "encode_envelope_frame",
]
