"""Client-side WebSocket transport for ASAP JSON-RPC 2.0 traffic.

:class:`WebSocketTransport` manages connection lifecycle, reconnect behavior,
send/receive helpers, ADR-16 acknowledgement tracking, and inbound frame routing.
Tests can patch ``asap.transport.ws.client.websockets.connect`` and
``asap.transport.ws.codecs.encode_envelope_frame`` because this module reads
those attributes at call time.
"""

from __future__ import annotations

import asyncio
import itertools
import ssl
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from typing import TYPE_CHECKING, Any, Union, cast

import websockets

from asap.models.envelope import Envelope
from asap.observability import get_logger
from asap.transport.circuit_breaker import CircuitBreaker
from asap.transport.errors import assert_stream_correlation_binds
from asap.transport.ws._ack import PendingAck, _AckRetransmit
from asap.transport.ws._errors import WebSocketRemoteError
from asap.transport.ws._recv import _RecvDispatch

# Module alias: names read through ``_ws_codecs`` at call time stay patchable
# on ``asap.transport.ws.codecs``.
from asap.transport.ws import codecs as _ws_codecs
from asap.transport.ws.codecs import (
    DEFAULT_WS_RECEIVE_TIMEOUT,
    RECONNECT_INITIAL_BACKOFF,
    RECONNECT_MAX_BACKOFF,
    ASAP_ACK_METHOD,
    _is_heartbeat_pong,
    decode_frame_to_json,
)

if TYPE_CHECKING:
    from websockets.legacy.client import WebSocketClientProtocol

logger = get_logger(__name__)


# Optional callback for server-push messages (sync or async).
OnMessageCallback = Union[Callable[[Envelope], None], Callable[[Envelope], Awaitable[None]]]


def _reconnect_delay(
    attempt: int,
    initial_backoff: float = RECONNECT_INITIAL_BACKOFF,
    max_backoff: float = RECONNECT_MAX_BACKOFF,
) -> float:
    """Exponential backoff delay for reconnection attempt *attempt* (1-indexed)."""
    return float(min(initial_backoff * (2 ** (attempt - 1)), max_backoff))


class WebSocketTransport(_RecvDispatch, _AckRetransmit):
    """Async WebSocket client transport with ack-aware retransmit (ADR-16).

    Composes :class:`_RecvDispatch` (inbound frame routing) and
    :class:`_AckRetransmit` (pending-ack retransmit). Owns connection lifecycle,
    the collapsed send preamble (:meth:`_send_frame`), and public send/receive
    methods.

    Example:
        >>> async with WebSocketTransport() as t:
        ...     await t.connect("ws://localhost:8080/asap/ws")
        ...     await t.send(envelope)
    """

    def __init__(
        self,
        receive_timeout: float = DEFAULT_WS_RECEIVE_TIMEOUT,
        on_message: OnMessageCallback | None = None,
        ping_interval: float | None = None,
        ping_timeout: float | None = 20.0,
        reconnect_on_disconnect: bool = False,
        max_reconnect_attempts: int | None = None,
        initial_backoff: float = RECONNECT_INITIAL_BACKOFF,
        max_backoff: float = RECONNECT_MAX_BACKOFF,
        ack_timeout_seconds: float = 30.0,
        max_ack_retries: int = 3,
        circuit_breaker: CircuitBreaker | None = None,
        ack_check_interval: float = 5.0,
        ssl_context: ssl.SSLContext | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self._ws: WebSocketClientProtocol | None = None
        self._receive_timeout = receive_timeout
        self._on_message = on_message
        self._ping_interval = ping_interval
        self._ping_timeout = ping_timeout
        self._reconnect_on_disconnect = reconnect_on_disconnect
        self._max_reconnect_attempts = max_reconnect_attempts
        self._initial_backoff = initial_backoff
        self._max_backoff = max_backoff
        self._ack_timeout_seconds = ack_timeout_seconds
        self._max_ack_retries = max_ack_retries
        self._circuit_breaker = circuit_breaker
        self._ack_check_interval = ack_check_interval
        self._request_counter = itertools.count(1)
        self._pending: dict[str, asyncio.Future[Envelope]] = {}
        # JSON-RPC request_id -> request envelope id so the recv loop can enforce
        # response.correlation_id binding (B6/BUG #6). Kept separate from
        # ``_pending`` to avoid disturbing the future cleanup paths in close().
        self._pending_request_ids: dict[str, str] = {}
        self._pending_acks: dict[str, PendingAck] = {}
        self._recv_task: asyncio.Task[None] | None = None
        self._ack_check_task: asyncio.Task[None] | None = None
        self._run_task: asyncio.Task[None] | None = None
        self._closed = False
        self._connected_event: asyncio.Event = asyncio.Event()
        self._connect_error: Exception | None = None
        self._ssl_context = ssl_context
        # Headers applied to every WS handshake (e.g. ``Authorization: Bearer …``)
        # so OAuth2-only deployments don't reject the connection with 4401 (CR#3).
        self._extra_headers: dict[str, str] = dict(extra_headers) if extra_headers else {}
        self._connect_lock = asyncio.Lock()

    async def _do_connect(self, url: str) -> None:
        if self._ws is not None:
            return
        await self._cancel_ack_check_task()
        self._connect_error = None
        logger.info("asap.websocket.client_connecting", url=url)
        # ``websockets.connect`` is looked up at call time so tests patching
        # ``asap.transport.ws.client.websockets.connect`` stay effective.
        connect_kwargs: dict[str, Any] = {
            "open_timeout": 10.0,
            "close_timeout": 5.0,
            "ping_interval": self._ping_interval,
            "ping_timeout": self._ping_timeout,
        }
        if self._ssl_context is not None:
            connect_kwargs["ssl"] = self._ssl_context
        if self._extra_headers:
            connect_kwargs["extra_headers"] = self._extra_headers
        self._ws = cast(Any, await websockets.connect(url, **connect_kwargs))
        if self._closed:
            await self._close_ws()
            return
        self._recv_task = asyncio.create_task(self._recv_loop())
        self._ack_check_task = asyncio.create_task(self._ack_check_loop())
        self._connected_event.set()
        logger.info("asap.websocket.client_connected", url=url)

    async def _cancel_ack_check_task(self) -> None:
        if self._ack_check_task is None:
            return
        self._ack_check_task.cancel()
        with suppress(asyncio.CancelledError):
            await self._ack_check_task
        self._ack_check_task = None

    async def _close_ws(self) -> None:
        ws = self._ws
        if ws is not None:
            await ws.close()
        self._ws = None

    async def _run_loop(self, url: str) -> None:
        attempt = 0
        while not self._closed:
            self._connected_event.clear()
            try:
                await self._do_connect(url)
            except Exception as e:
                if attempt == 0:
                    self._connect_error = e
                    self._connected_event.set()
                    raise
                logger.warning(
                    "asap.websocket.reconnect_connect_error", error=str(e), attempt=attempt
                )
                self._ws = None
                self._recv_task = None
                attempt += 1
                if self._reconnect_exhausted(attempt):
                    return
                await asyncio.sleep(
                    _reconnect_delay(attempt, self._initial_backoff, self._max_backoff)
                )
                continue
            if attempt > 0:
                logger.info("asap.websocket.reconnected", url=url, attempt=attempt)
            if self._recv_task is not None:
                with suppress(asyncio.CancelledError):
                    await self._recv_task
            if self._closed:
                break
            await self._close_ws()
            self._recv_task = None
            attempt += 1
            if self._reconnect_exhausted(attempt):
                logger.warning("asap.websocket.reconnect_max_attempts", attempt=attempt)
                break
            delay = _reconnect_delay(attempt, self._initial_backoff, self._max_backoff)
            logger.debug("asap.websocket.reconnect_backoff", delay=delay, attempt=attempt)
            await asyncio.sleep(delay)

    def _reconnect_exhausted(self, attempt: int) -> bool:
        return self._max_reconnect_attempts is not None and attempt >= self._max_reconnect_attempts

    async def connect(self, url: str) -> None:
        async with self._connect_lock:
            if self._ws is not None:
                return
            self._closed = False
            if self._reconnect_on_disconnect:
                self._run_task = asyncio.create_task(self._run_loop(url))
                await self._connected_event.wait()
                if self._connect_error is not None:
                    err = self._connect_error
                    self._connect_error = None
                    if self._run_task is not None:
                        with suppress(asyncio.CancelledError):
                            await self._run_task
                        self._run_task = None
                    raise err
            else:
                await self._do_connect(url)

    async def close(self) -> None:
        self._closed = True
        self._connected_event.set()
        await self._cancel_task(self._ack_check_task)
        self._ack_check_task = None
        await self._cancel_task(self._run_task)
        self._run_task = None
        await self._cancel_task(self._recv_task)
        self._recv_task = None
        self._pending_acks.clear()
        for future in self._pending.values():
            if not future.done():
                future.set_exception(asyncio.TimeoutError("Connection closed"))
        self._pending.clear()
        self._pending_request_ids.clear()
        if self._ws is not None:
            with suppress(OSError):
                await self._ws.close()
            self._ws = None
            logger.debug("asap.websocket.client_closed")

    @staticmethod
    async def _cancel_task(task: asyncio.Task[None] | None) -> None:
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def __aenter__(self) -> "WebSocketTransport":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    def _next_request_id(self) -> str:
        return f"ws-req-{next(self._request_counter)}"

    def _envelope_dict_for_send(self, envelope: Envelope) -> dict[str, Any]:
        dump = envelope.model_dump(mode="json")
        if (
            not dump.get("requires_ack")
            and dump.get("payload_type") in _ws_codecs.PAYLOAD_TYPES_REQUIRING_ACK
        ):
            dump = {**dump, "requires_ack": True}
        return dump

    async def _send_frame(self, envelope: Envelope, *, register_ack: bool) -> str:
        """Encode *envelope* and send one JSON-RPC text frame; return the request id.

        Shared by ``send``, ``send_and_receive``, ``send_and_receive_stream``,
        and the retransmit path. Reads ``encode_envelope_frame`` off the codecs
        module at call time so tests can patch
        ``asap.transport.ws.codecs.encode_envelope_frame``.
        """
        if self._ws is None:
            raise RuntimeError("WebSocket not connected; call connect(url) first")
        request_id = self._next_request_id()
        frame = _ws_codecs.encode_envelope_frame(
            self._envelope_dict_for_send(envelope), request_id=request_id
        )
        if not isinstance(frame, str):
            raise TypeError(f"Expected text frame (str), got {type(frame).__name__}")
        await self._ws.send(frame)
        if register_ack:
            self._register_pending_ack(envelope)
        return request_id

    async def send(self, envelope: Envelope) -> None:
        """Send *envelope* and register a pending ack if it requires one."""
        await self._send_frame(envelope, register_ack=True)

    async def send_and_receive(self, envelope: Envelope) -> Envelope:
        """Send *envelope* and await its correlated response envelope."""
        request_id = await self._send_frame(envelope, register_ack=True)
        future: asyncio.Future[Envelope] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        self._pending_request_ids[request_id] = str(envelope.id)
        try:
            return await asyncio.wait_for(future, timeout=self._receive_timeout)
        finally:
            self._pending.pop(request_id, None)
            self._pending_request_ids.pop(request_id, None)

    async def send_and_receive_stream(self, envelope: Envelope) -> AsyncIterator[Envelope]:
        """Send one JSON-RPC frame and yield each streaming ``result.envelope``.

        Stops after an envelope whose payload has ``final=True`` (e.g. ``TaskStream``).
        Skips heartbeat pongs and ``asap.ack`` notifications. Streamed response
        payloads and ``TaskStream`` chunks are correlation-bound to
        ``envelope.id`` before they are yielded (CR#4).
        """
        request_id = await self._send_frame(envelope, register_ack=True)
        ws = self._ws
        assert ws is not None  # _send_frame raises if not connected
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=self._receive_timeout)
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            data = decode_frame_to_json(raw)
            if _is_heartbeat_pong(data) or data.get("method") == ASAP_ACK_METHOD:
                continue
            if "error" in data:
                err = data["error"]
                raise WebSocketRemoteError(
                    err.get("code", -32603),
                    err.get("message", "Unknown error"),
                    err.get("data"),
                )
            if data.get("id") != request_id:
                continue
            result = data.get("result")
            if not result or "envelope" not in result:
                raise WebSocketRemoteError(-32603, "Missing result.envelope in response", data=data)
            env = Envelope.model_validate(result["envelope"])
            # BINDING: WS streaming frames must still belong to the request
            # that opened this stream, not some concurrent request.
            assert_stream_correlation_binds(str(envelope.id), env)
            yield env
            if env.payload_dict.get("final") is True:
                break

    async def receive(self) -> Envelope:
        """Receive the next response envelope (no request correlation)."""
        if self._ws is None:
            raise RuntimeError("WebSocket not connected; call connect(url) first")
        raw = await asyncio.wait_for(self._ws.recv(), timeout=self._receive_timeout)
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        data = decode_frame_to_json(raw)
        if "error" in data:
            err = data["error"]
            raise WebSocketRemoteError(
                err.get("code", -32603),
                err.get("message", "Unknown error"),
                err.get("data"),
            )
        result = data.get("result")
        if not result or "envelope" not in result:
            raise WebSocketRemoteError(-32603, "Missing result.envelope in response", data=data)
        return Envelope.model_validate(result["envelope"])


__all__ = [
    "OnMessageCallback",
    "PendingAck",
    "WebSocketRemoteError",
    "WebSocketTransport",
    "_reconnect_delay",
]
