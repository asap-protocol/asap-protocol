"""ASAP WebSocket transport package.

The package provides JSON-RPC 2.0 over WebSocket support:

- :mod:`asap.transport.ws.codecs` defines frame codecs and constants.
- :mod:`asap.transport.ws.client` provides :class:`WebSocketTransport`.
- :mod:`asap.transport.ws._recv` routes inbound client frames.
- :mod:`asap.transport.ws._ack` tracks pending ADR-16 acknowledgements.
- :mod:`asap.transport.ws._errors` defines :class:`WebSocketRemoteError`.
- :mod:`asap.transport.ws.pool` provides :class:`WebSocketConnectionPool`.
- :mod:`asap.transport.ws.server` handles server connections and heartbeats.
- :mod:`asap.transport.ws._actions` defines close decisions.
- :mod:`asap.transport.ws._dispatch` dispatches WebSocket envelopes.

Import the WebSocket transport surface from this package.
"""

from __future__ import annotations

from asap.transport.ws._actions import WSCloseAction
from asap.transport.ws._ack import PendingAck
from asap.transport.ws._errors import WebSocketRemoteError
from asap.transport.ws.client import (
    OnMessageCallback,
    WebSocketTransport,
    _reconnect_delay,
)
from asap.transport.ws.codecs import (
    ACK_CHECK_INTERVAL,
    ASAP_ACK_METHOD,
    DEFAULT_ACK_TIMEOUT,
    DEFAULT_MAX_ACK_RETRIES,
    DEFAULT_POOL_IDLE_TIMEOUT,
    DEFAULT_POOL_MAX_SIZE,
    DEFAULT_WS_RECEIVE_TIMEOUT,
    FRAME_ENCODING_JSON,
    HEARTBEAT_FRAME_TYPE_PING,
    HEARTBEAT_FRAME_TYPE_PONG,
    HEARTBEAT_PING_INTERVAL,
    PAYLOAD_TYPES_REQUIRING_ACK,
    RECONNECT_INITIAL_BACKOFF,
    RECONNECT_MAX_BACKOFF,
    SLA_BREACH_NOTIFICATION_METHOD,
    SLA_SUBSCRIBE_METHOD,
    SLA_UNSUBSCRIBE_METHOD,
    STALE_CONNECTION_TIMEOUT,
    WS_CLOSE_AUTH_REQUIRED,
    WS_CLOSE_GOING_AWAY,
    WS_CLOSE_POLICY_VIOLATION,
    WS_CLOSE_REASON_SHUTDOWN,
    _build_ack_notification_frame,
    _is_heartbeat_pong,
    decode_frame_to_json,
    encode_envelope_frame,
)
from asap.transport.ws.pool import WebSocketConnectionPool
from asap.transport.ws.server import (
    _heartbeat_loop,
    _process_ws_message,
    broadcast_sla_breach,
    handle_websocket_connection,
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
    "OnMessageCallback",
    "PAYLOAD_TYPES_REQUIRING_ACK",
    "PendingAck",
    "RECONNECT_INITIAL_BACKOFF",
    "RECONNECT_MAX_BACKOFF",
    "SLA_BREACH_NOTIFICATION_METHOD",
    "SLA_SUBSCRIBE_METHOD",
    "SLA_UNSUBSCRIBE_METHOD",
    "STALE_CONNECTION_TIMEOUT",
    "WSCloseAction",
    "WS_CLOSE_AUTH_REQUIRED",
    "WS_CLOSE_GOING_AWAY",
    "WS_CLOSE_POLICY_VIOLATION",
    "WS_CLOSE_REASON_SHUTDOWN",
    "WebSocketConnectionPool",
    "WebSocketRemoteError",
    "WebSocketTransport",
    "_build_ack_notification_frame",
    "_heartbeat_loop",
    "_is_heartbeat_pong",
    "_process_ws_message",
    "_reconnect_delay",
    "broadcast_sla_breach",
    "decode_frame_to_json",
    "encode_envelope_frame",
    "handle_websocket_connection",
]
