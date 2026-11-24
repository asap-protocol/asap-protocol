"""Server-side WebSocket handling for ASAP JSON-RPC 2.0 traffic.

This module accepts WebSocket connections, enforces optional OAuth2 bearer
authentication, runs heartbeat and stale-connection checks, applies per-connection
rate limiting, dispatches ASAP envelopes, and broadcasts SLA breach notifications.

``_heartbeat_loop`` reads ``HEARTBEAT_PING_INTERVAL`` off the codecs module at
call time so tests can patch ``asap.transport.ws.codecs.HEARTBEAT_PING_INTERVAL``.
"""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from fastapi import WebSocket

from asap.observability import get_logger
from asap.transport.rate_limit import (
    DEFAULT_WS_MESSAGES_PER_SECOND,
    WebSocketTokenBucket,
)
from asap.transport.ws._actions import WSCloseAction, _ws_close_code
from asap.transport.ws._dispatch import (
    _dispatch_ws_envelope,
    _extract_envelope_for_ack,
    _maybe_send_received_ack,
    _send_rate_limit_error,
)

# Module alias: names read through ``_ws_codecs`` at call time stay patchable
# on ``asap.transport.ws.codecs``.
from asap.transport.ws import codecs as _ws_codecs
from asap.transport.ws.codecs import (
    HEARTBEAT_FRAME_TYPE_PING,
    SLA_BREACH_NOTIFICATION_METHOD,
    SLA_SUBSCRIBE_METHOD,
    SLA_UNSUBSCRIBE_METHOD,
    STALE_CONNECTION_TIMEOUT,
    WS_CLOSE_AUTH_REQUIRED,
    _is_heartbeat_pong,
)

if TYPE_CHECKING:
    from asap.auth.middleware import OAuth2Middleware
    from asap.economics.sla import SLABreach
    from asap.transport.server import ASAPRequestHandler

logger = get_logger(__name__)


def _bearer_token_from_websocket(websocket: WebSocket) -> str | None:
    """Return the raw Bearer token from the WS ``Authorization`` header, if any.

    WS subprotocols cannot carry per-message auth headers, so the Bearer MUST be
    presented in the handshake ``Authorization`` header (kept in
    ``websocket.scope["headers"]`` by Starlette).
    """
    auth = websocket.headers.get("Authorization")
    if not auth or not auth.startswith("Bearer "):
        return None
    return auth[7:].strip() or None


async def _enforce_ws_oauth2(
    websocket: WebSocket,
    oauth2_middleware: "OAuth2Middleware",
) -> bool:
    """Validate the WS Bearer JWT at acceptance; close 4401 on failure.

    Returns ``True`` when the connection is admitted, ``False`` after closing it.
    Mirrors :class:`OAuth2Middleware` HTTP semantics so a WS-only deployment can
    no longer bypass IdP JWT validation. Unlike the HTTP path, the WS path
    validates the Bearer token once at connection acceptance; token expiry or
    scope loss takes effect on the next connection.
    """
    token = _bearer_token_from_websocket(websocket)
    path = websocket.scope.get("path", "/asap/ws")
    if not token:
        logger.warning("asap.oauth2.missing_token", path=path)
        await websocket.close(code=WS_CLOSE_AUTH_REQUIRED, reason="Authentication required")
        return False
    _claims, error = await oauth2_middleware.validate_bearer_token(token, path=path)
    if error is not None:
        # The middleware returns 401/403 JSON for HTTP; over WS we surface the
        # failure as a close with the auth code rather than a JSON body, since
        # the client has not yet sent any JSON-RPC frame to correlate.
        await websocket.close(code=WS_CLOSE_AUTH_REQUIRED, reason="Invalid token")
        return False
    return True


async def _heartbeat_loop(
    websocket: WebSocket,
    last_received: list[float],
    closed: asyncio.Event,
) -> None:
    """Server-initiated heartbeat: ping every N seconds, evict stale connections.

    Reads ``HEARTBEAT_PING_INTERVAL`` off the codecs module at call time so tests
    patching ``asap.transport.ws.codecs.HEARTBEAT_PING_INTERVAL`` stay effective.
    """
    ping_interval = _ws_codecs.HEARTBEAT_PING_INTERVAL
    stale_timeout = STALE_CONNECTION_TIMEOUT
    while not closed.is_set():
        try:
            await asyncio.sleep(ping_interval)
            if closed.is_set():
                return
            now = time.monotonic()
            if now - last_received[0] > stale_timeout:
                logger.info("asap.websocket.stale_connection", idle_seconds=now - last_received[0])
                closed.set()
                return
            await websocket.send_text(json.dumps({"type": HEARTBEAT_FRAME_TYPE_PING}))
        except asyncio.CancelledError:
            return
        except (SystemExit, KeyboardInterrupt):
            raise
        except Exception as e:
            logger.debug("asap.websocket.heartbeat_error", error=str(e))
            closed.set()
            return


class _WSSubscriptionDispatch:
    """Method-to-handler table for SLA breach subscribe/unsubscribe.

    Each handler mutates the subscriber set and replies with a JSON-RPC result;
    unknown methods fall through to normal envelope dispatch.
    """

    def __init__(self, subscribers: set[WebSocket]) -> None:
        self._subscribers = subscribers
        self._handlers: dict[str, Any] = {
            SLA_SUBSCRIBE_METHOD: self._handle_subscribe,
            SLA_UNSUBSCRIBE_METHOD: self._handle_unsubscribe,
        }

    def handles(self, method: Any) -> bool:
        return method in self._handlers

    async def dispatch(self, websocket: WebSocket, data: dict[str, Any]) -> None:
        """Run the handler registered for ``data["method"]`` (caller pre-checks :meth:`handles`)."""
        method = data.get("method")
        handler = self._handlers.get(method) if isinstance(method, str) else None
        if handler is None:
            return
        await handler(websocket, data.get("id"))

    async def _handle_subscribe(self, websocket: WebSocket, request_id: Any) -> None:
        self._subscribers.add(websocket)
        await websocket.send_text(
            json.dumps({"jsonrpc": "2.0", "result": {"subscribed": True}, "id": request_id})
        )

    async def _handle_unsubscribe(self, websocket: WebSocket, request_id: Any) -> None:
        self._subscribers.discard(websocket)
        await websocket.send_text(
            json.dumps({"jsonrpc": "2.0", "result": {"unsubscribed": True}, "id": request_id})
        )


async def broadcast_sla_breach(
    breach: "SLABreach",
    subscribers: set[WebSocket],
) -> None:
    """Send an SLA breach notification to all subscribed WebSocket connections.

    Each subscriber receives a JSON-RPC 2.0 notification (no id): method
    ``sla.breach``, ``params.breach`` contains the breach payload (serialized
    from :class:`SLABreach`).

    Args:
        breach: The breach to broadcast.
        subscribers: Set of WebSocket connections subscribed to SLA breach events.
    """
    payload = {
        "jsonrpc": "2.0",
        "method": SLA_BREACH_NOTIFICATION_METHOD,
        "params": {"breach": breach.model_dump(mode="json")},
    }
    text = json.dumps(payload, default=str)

    async def _safe_send(ws: WebSocket) -> None:
        try:
            await ws.send_text(text)
        except (RuntimeError, OSError) as e:
            logger.debug("asap.websocket.sla_breach_send_error", error=str(e))
            subscribers.discard(ws)

    await asyncio.gather(*(_safe_send(ws) for ws in list(subscribers)))


async def _process_ws_message(
    raw: str,
    websocket: WebSocket,
    request_handler: "ASAPRequestHandler",
    bucket: WebSocketTokenBucket | None,
    subscriptions: _WSSubscriptionDispatch | None,
) -> WSCloseAction:
    """Handle one inbound WS text frame and return the close action for the loop.

    Parses the frame, routes SLA subscribe/unsubscribe through *subscriptions*
    enforces the per-connection rate limit, sends the ``received`` ack when
    required, and dispatches the envelope. Returns ``CLOSE_RATE_LIMITED`` when
    the bucket is empty, ``CLOSE_FATAL`` on a fatal send failure, else
    ``CONTINUE``.
    """
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, RecursionError):
        return WSCloseAction.CONTINUE
    if _is_heartbeat_pong(data):
        return WSCloseAction.CONTINUE
    if (
        isinstance(data, dict)
        and subscriptions is not None
        and subscriptions.handles(data.get("method"))
    ):
        await subscriptions.dispatch(websocket, data)
        return WSCloseAction.CONTINUE
    if bucket is not None and not bucket.consume(1):
        await _send_rate_limit_error(websocket, bucket, data)
        return WSCloseAction.CLOSE_RATE_LIMITED
    envelope_for_ack = _extract_envelope_for_ack(data)
    await _maybe_send_received_ack(websocket, envelope_for_ack)
    return await _dispatch_ws_envelope(websocket, request_handler, raw, data, envelope_for_ack)


async def handle_websocket_connection(
    websocket: WebSocket,
    request_handler: "ASAPRequestHandler",
    active_connections: set[WebSocket] | None = None,
    ws_message_rate_limit: float | None = DEFAULT_WS_MESSAGES_PER_SECOND,
    sla_breach_subscribers: set[WebSocket] | None = None,
    oauth2_middleware: "OAuth2Middleware | None" = None,
) -> None:
    """Serve one WebSocket connection: auth, heartbeat, rate-limited envelope dispatch.

    OAuth2-only deployments must not admit an unauthenticated WS: the HTTP
    middleware stack never runs over WebSocket, so :func:`_enforce_ws_oauth2`
    validates the IdP JWT explicitly at acceptance. When ``manifest.auth`` is
    the active auth path, ``oauth2_middleware`` is None and the
    request-preparation pipeline handles auth via ``_prepare_request``.
    """
    await websocket.accept()
    if oauth2_middleware is not None and not await _enforce_ws_oauth2(websocket, oauth2_middleware):
        return
    if active_connections is not None:
        active_connections.add(websocket)
    logger.info("asap.websocket.connected", client=websocket.client)
    last_received: list[float] = [time.monotonic()]
    closed = asyncio.Event()
    bucket: WebSocketTokenBucket | None = (
        WebSocketTokenBucket(rate=ws_message_rate_limit)
        if ws_message_rate_limit is not None and ws_message_rate_limit > 0
        else None
    )
    subscriptions = (
        _WSSubscriptionDispatch(sla_breach_subscribers)
        if sla_breach_subscribers is not None
        else None
    )
    close_action = WSCloseAction.CLOSE_FATAL
    heartbeat_task: asyncio.Task[None] | None = None
    try:
        heartbeat_task = asyncio.create_task(_heartbeat_loop(websocket, last_received, closed))
        close_action = await _ws_receive_loop(
            websocket, request_handler, closed, last_received, bucket, subscriptions
        )
    except (SystemExit, KeyboardInterrupt):
        raise
    except Exception as e:
        logger.warning("asap.websocket.connection_error", error=str(e))
    finally:
        await _teardown_ws_connection(
            closed, heartbeat_task, active_connections, sla_breach_subscribers, websocket
        )
        try:
            await websocket.close(code=_ws_close_code(close_action))
        except (OSError, RuntimeError) as close_err:
            logger.debug("asap.websocket.close_error", error=str(close_err))
        logger.info("asap.websocket.closed", client=websocket.client)


async def _teardown_ws_connection(
    closed: asyncio.Event,
    heartbeat_task: asyncio.Task[None] | None,
    active_connections: set[WebSocket] | None,
    sla_breach_subscribers: set[WebSocket] | None,
    websocket: WebSocket,
) -> None:
    """Cancel the heartbeat, signal closure, and drop the connection from bookkeeping sets."""
    closed.set()
    if heartbeat_task is not None:
        heartbeat_task.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat_task
    if active_connections is not None:
        active_connections.discard(websocket)
    if sla_breach_subscribers is not None:
        sla_breach_subscribers.discard(websocket)


async def _ws_receive_loop(
    websocket: WebSocket,
    request_handler: "ASAPRequestHandler",
    closed: asyncio.Event,
    last_received: list[float],
    bucket: WebSocketTokenBucket | None,
    subscriptions: _WSSubscriptionDispatch | None,
) -> WSCloseAction:
    """Drive the receive loop until close; return the terminal close action."""
    action = WSCloseAction.CONTINUE
    while not closed.is_set():
        try:
            raw = await websocket.receive_text()
        except (SystemExit, KeyboardInterrupt):
            raise
        except Exception as e:
            logger.warning("asap.websocket.receive_error", error=str(e))
            return WSCloseAction.CLOSE_FATAL
        last_received[0] = time.monotonic()
        action = await _process_ws_message(raw, websocket, request_handler, bucket, subscriptions)
        if action is WSCloseAction.CONTINUE:
            continue
        return action
    return action


__all__ = [
    "WSCloseAction",
    "_bearer_token_from_websocket",
    "_enforce_ws_oauth2",
    "_heartbeat_loop",
    "_process_ws_message",
    "broadcast_sla_breach",
    "handle_websocket_connection",
]
