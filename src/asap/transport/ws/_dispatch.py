"""Envelope dispatch layer for the WebSocket server handler.

WebSocket frames are prepared through the shared request pipeline and then
dispatched through the handler registry. The helpers in this module send
JSON-RPC result, error, ack, and streaming frames back over the WebSocket.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any, cast

from fastapi import WebSocket
from fastapi.responses import Response
from pydantic import ValidationError
from starlette.requests import Request

from asap.errors import ASAPError
from asap.models.envelope import Envelope
from asap.observability import get_logger, is_debug_mode
from asap.observability.tracing import inject_envelope_trace_context
from asap.transport.jsonrpc import (
    INTERNAL_ERROR,
    JsonRpcErrorResponse,
    JsonRpcError,
    JsonRpcResponse,
)
from asap.transport.rate_limit import WebSocketTokenBucket
from asap.transport.ws._actions import WSCloseAction
from asap.transport.ws.codecs import _build_ack_notification_frame
from fastapi.responses import JSONResponse
from opentelemetry import context

if TYPE_CHECKING:
    from asap.transport.server import ASAPRequestHandler, PreparedRequest

logger = get_logger(__name__)


def _is_prepared_request(prepared: Any) -> bool:
    """Structural check distinguishing a ``PreparedRequest`` from an error ``Response``.

    ``_prepare_request`` returns either a :class:`PreparedRequest` (has
    ``envelope``/``ctx``/``trace_token``) or a JSON-RPC error
    :class:`fastapi.responses.Response` (has ``body``, no ``envelope``). We avoid
    importing ``PreparedRequest`` at module top to break a circular import
    (``asap.transport.server`` → ``routes`` → ``asap.transport.ws`` → this
    module), so the check is structural.
    """
    return hasattr(prepared, "envelope") and hasattr(prepared, "ctx")


def _registry_has_streaming_for_payload(registry: Any, payload_type: str) -> bool:
    """True only when registry reports streaming with a real bool (not e.g. MagicMock)."""
    fn = getattr(registry, "has_streaming_handler", None)
    if not callable(fn):
        return False
    return fn(payload_type) is True


async def _synthesize_ws_request(body: str, websocket: WebSocket) -> Request:
    """Build a minimal Starlette ``Request`` carrying *body* for the shared pipeline.

    The shared :meth:`ASAPRequestHandler._prepare_request` pipeline reads the
    JSON-RPC body via ``request.stream()``, so the WS path provides a ``Request``.
    The scope mirrors the WS handshake (headers, path, server) so auth and
    content-length checks behave like the HTTP path.
    """
    body_bytes = body.encode("utf-8")
    headers = list(websocket.scope.get("headers", []))
    headers.append((b"content-length", str(len(body_bytes)).encode()))
    scope: dict[str, Any] = {
        "type": "http",
        "method": "POST",
        "headers": headers,
        "path": websocket.scope.get("path", "/asap"),
        "root_path": "",
        "query_string": b"",
        "server": websocket.scope.get("server", ("localhost", 8000)),
        # Mirror the WS handshake's app + client so _prepare_request reads
        # (e.g. request.app.state) and sender extraction behave like the HTTP path.
        "app": websocket.scope.get("app"),
        "client": websocket.scope.get("client"),
    }
    first_receive = True

    async def receive() -> dict[str, Any]:
        nonlocal first_receive
        if first_receive:
            first_receive = False
            return {"type": "http.request", "body": body_bytes, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(_: Any) -> None:
        pass

    return Request(scope, receive, send)


async def _send_rate_limit_error(
    websocket: WebSocket, bucket: WebSocketTokenBucket, data: dict[str, Any]
) -> None:
    """Send the JSON-RPC rate-limit error frame and log the exceeded event."""
    logger.warning(
        "asap.websocket.rate_limit_exceeded",
        client=websocket.client,
        limit_per_sec=bucket.rate,
    )
    await websocket.send_text(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "error": {
                    "code": -32001,
                    "message": "Rate limit exceeded; too many messages per second",
                },
                "id": data.get("id"),
            }
        )
    )


def _extract_envelope_for_ack(data: dict[str, Any]) -> Envelope | None:
    """Validate the envelope in *data*'s params for ack routing; None if absent/invalid."""
    params = data.get("params")
    envelope_dict = params.get("envelope") if isinstance(params, dict) else None
    if not isinstance(envelope_dict, dict):
        return None
    try:
        return Envelope.model_validate(envelope_dict)
    except ValidationError as e:
        logger.warning("asap.websocket.envelope_validation_failed", error=str(e))
        return None


async def _maybe_send_received_ack(websocket: WebSocket, envelope_for_ack: Envelope | None) -> None:
    """Send a ``received`` ack frame when *envelope_for_ack* requires one."""
    if envelope_for_ack is None or not envelope_for_ack.requires_ack or not envelope_for_ack.id:
        return
    ack_frame = _build_ack_notification_frame(
        original_envelope_id=envelope_for_ack.id,
        status="received",
        sender=envelope_for_ack.recipient,
        recipient=envelope_for_ack.sender,
        asap_version=envelope_for_ack.asap_version,
    )
    await websocket.send_text(ack_frame)


async def _dispatch_ws_envelope(
    websocket: WebSocket,
    request_handler: "ASAPRequestHandler",
    raw: str,
    data: dict[str, Any],
    envelope_for_ack: Envelope | None,
) -> WSCloseAction:
    """Prepare the request and dispatch its envelope.

    Uses :meth:`ASAPRequestHandler._prepare_request` to run the shared
    parse/auth/envelope/timestamp/nonce gate, then hands the validated envelope
    to ``registry.dispatch_async`` or ``dispatch_stream_async``. Sends a
    ``rejected`` ack and a JSON-RPC error frame on failure. Returns
    ``CLOSE_FATAL`` if the internal-error frame itself cannot be sent
    (connection broken), else ``CONTINUE``.
    """
    jsonrpc_id = data.get("id")
    response_id: str | int = jsonrpc_id if jsonrpc_id is not None else ""
    prepared: Any = None
    try:
        request = await _synthesize_ws_request(raw, websocket)
        prepared = await request_handler._prepare_request(  # noqa: SLF001 — public dispatch seam
            request, time.perf_counter(), received_log_event="asap.request.ws_received"
        )
        if _is_prepared_request(prepared):
            await _run_ws_dispatch(
                websocket, request_handler, cast("PreparedRequest", prepared), response_id
            )
            return WSCloseAction.CONTINUE
        # Preparation failed: forward the JSON-RPC error response body over WS.
        body = cast("Response", prepared).body
        await websocket.send_text(body.decode("utf-8") if isinstance(body, bytes) else str(body))
        return WSCloseAction.CONTINUE
    except ASAPError as e:
        ctx = cast("PreparedRequest", prepared).ctx if _is_prepared_request(prepared) else None
        logger.warning("asap.websocket.protocol_error", error=str(e), error_type=type(e).__name__)
        await _send_ws_dispatch_failure(websocket, envelope_for_ack, e)
        if ctx is not None:
            asap_payload_type = (
                envelope_for_ack.payload_type if envelope_for_ack is not None else ""
            )
            await _send_ws_asap_error_frame(
                websocket, request_handler, ctx, e, payload_type=asap_payload_type
            )
        return WSCloseAction.CONTINUE
    except Exception as e:
        ctx = cast("PreparedRequest", prepared).ctx if _is_prepared_request(prepared) else None
        payload_type = envelope_for_ack.payload_type if envelope_for_ack is not None else ""
        logger.warning("asap.websocket.message_error", error=str(e))
        await _send_ws_dispatch_failure(websocket, envelope_for_ack, e)
        return await _send_internal_error_frame(
            websocket, e, ctx=ctx, payload_type=payload_type, response_id=response_id
        )
    finally:
        if _is_prepared_request(prepared):
            context.detach(cast("PreparedRequest", prepared).trace_token)


async def _run_ws_dispatch(
    websocket: WebSocket,
    request_handler: "ASAPRequestHandler",
    prepared: PreparedRequest,
    response_id: str | int,
) -> None:
    """Dispatch the prepared envelope with HTTP-parity error handling and observability.

    Routes through :meth:`ASAPRequestHandler._dispatch_to_handler` so
    ``HandlerNotFoundError`` and ``ThreadPoolExhaustedError`` map to the same
    JSON-RPC error codes / retry hints as the HTTP path (instead of collapsing
    to a generic ``-32603``). Records request-level success metrics + audit log
    on the happy path (parity with ``handle_message``). Any error response
    returned by the dispatch mapper is forwarded as a WS frame verbatim.
    """
    envelope = prepared.envelope
    ctx = prepared.ctx
    registry = request_handler.registry_holder.registry
    payload_type = envelope.payload_type
    if _registry_has_streaming_for_payload(registry, payload_type):
        await _run_ws_stream_dispatch(websocket, request_handler, registry, prepared, response_id)
        return
    dispatch_result = await request_handler._dispatch_to_handler(  # noqa: SLF001 — shared mapper
        envelope, ctx
    )
    if isinstance(dispatch_result, JSONResponse):
        # ``_dispatch_to_handler`` returns a non-JSON-RPC HTTP 503 body for
        # ``ThreadPoolExhaustedError`` (``error`` as a string). Forwarding that
        # verbatim over WS crashes the client recv loop (``_recv._resolve_error_frame``
        # calls ``data["error"].get("code")``). Re-frame it as a recoverable
        # JSON-RPC error preserving ``response_id`` + ``rpc_code`` + ``retry_after_ms``,
        # matching the HTTP semantics without the flat 503 schema (CR#2).
        if dispatch_result.status_code == 503:
            await _send_ws_recoverable_error_frame(websocket, dispatch_result, response_id)
            return
        await _send_ws_response_frame(websocket, dispatch_result)
        return
    response_envelope, payload_type = dispatch_result
    await _record_ws_success(
        websocket, request_handler, ctx, envelope, response_envelope, payload_type
    )
    await _send_ws_result_frame(websocket, response_envelope, response_id)


async def _run_ws_stream_dispatch(
    websocket: WebSocket,
    request_handler: "ASAPRequestHandler",
    registry: Any,
    prepared: PreparedRequest,
    response_id: str | int,
) -> None:
    """Stream-dispatch variant: yield one result frame per streamed chunk + final metrics."""
    envelope = prepared.envelope
    ctx = prepared.ctx
    manifest = request_handler.manifest
    payload_type = envelope.payload_type
    try:
        async for response_envelope in registry.dispatch_stream_async(envelope, manifest):
            await _send_ws_result_frame(websocket, response_envelope, response_id)
        await _record_ws_stream_success(request_handler, ctx, envelope, payload_type)
    except ASAPError as exc:
        await _send_ws_asap_error_frame(
            websocket, request_handler, ctx, exc, payload_type=payload_type
        )
    except Exception as exc:  # noqa: BLE001 — parity with HTTP internal-error handling
        await _send_internal_error_frame(
            websocket, exc, ctx=ctx, payload_type=payload_type, response_id=response_id
        )


async def _record_ws_success(
    websocket: WebSocket,
    request_handler: "ASAPRequestHandler",
    ctx: Any,
    envelope: Envelope,
    response_envelope: Envelope,
    payload_type: str,
) -> None:
    """Record request-level success metrics + audit log (parity with HTTP ``handle_message``)."""
    import time as _time

    duration_seconds = _time.perf_counter() - ctx.start_time
    normalized = request_handler._normalize_payload_type_for_metrics(payload_type)  # noqa: SLF001
    ctx.metrics.increment_counter(
        "asap_requests_total", {"payload_type": normalized, "status": "success"}
    )
    ctx.metrics.increment_counter("asap_requests_success_total", {"payload_type": normalized})
    ctx.metrics.observe_histogram(
        "asap_request_duration_seconds",
        duration_seconds,
        {"payload_type": normalized, "status": "success"},
    )
    try:
        from asap.transport._request_handler import _audit_log_operation

        await _audit_log_operation(
            _ws_app_state(websocket),
            operation=payload_type,
            agent_urn=envelope.sender,
            details={"envelope_id": envelope.id, "response_id": response_envelope.id},
        )
    except Exception:  # noqa: BLE001 — audit is best-effort
        logger.warning("asap.websocket.audit_failed", exc_info=True)


async def _record_ws_stream_success(
    request_handler: "ASAPRequestHandler",
    ctx: Any,
    envelope: Envelope,
    payload_type: str,
) -> None:
    """Record request-level success metrics for the streaming WS path."""
    import time as _time

    duration_seconds = _time.perf_counter() - ctx.start_time
    normalized = request_handler._normalize_payload_type_for_metrics(payload_type)  # noqa: SLF001
    ctx.metrics.increment_counter(
        "asap_requests_total", {"payload_type": normalized, "status": "success"}
    )
    ctx.metrics.increment_counter("asap_requests_success_total", {"payload_type": normalized})
    ctx.metrics.observe_histogram(
        "asap_request_duration_seconds",
        duration_seconds,
        {"payload_type": normalized, "status": "success"},
    )


def _ws_app_state(websocket: WebSocket) -> Any:
    """Return the websocket's app state (used for audit log injection)."""
    return websocket.app.state


async def _send_ws_response_frame(websocket: WebSocket, response: JSONResponse) -> None:
    """Forward an HTTP ``JSONResponse`` (already a JSON-RPC error/503 body) as a WS frame."""
    body = response.body
    await websocket.send_text(body.decode("utf-8") if isinstance(body, bytes) else str(body))


async def _send_ws_recoverable_error_frame(
    websocket: WebSocket,
    response: JSONResponse,
    response_id: str | int,
) -> None:
    """Re-frame an HTTP 503 (``ThreadPoolExhaustedError``) body as a WS JSON-RPC error.

    ``_dispatch_to_handler`` returns a non-JSON-RPC 503 body for thread-pool
    exhaustion (``error`` as a string). Sending that verbatim over WS crashes the
    client recv loop, which assumes ``error`` is a dict. This rebuilds a canonical
    JSON-RPC error frame preserving ``rpc_code`` / ``retry_after_ms`` / recovery
    hints from the 503 body and the request ``response_id`` (CR#2).
    """
    body = response.body
    raw = json.loads(body.decode("utf-8") if isinstance(body, bytes) else str(body))
    rpc_code = int(raw.get("rpc_code") or raw.get("code") or INTERNAL_ERROR)
    message = str(raw.get("message") or "Service Temporarily Unavailable")
    data: dict[str, Any] = {
        "recoverable": bool(raw.get("recoverable", True)),
    }
    if raw.get("retry_after_ms") is not None:
        data["retry_after_ms"] = raw["retry_after_ms"]
    if raw.get("fallback_action") is not None:
        data["fallback_action"] = raw["fallback_action"]
    if raw.get("details"):
        data["details"] = raw["details"]
    frame = JsonRpcErrorResponse(
        error=JsonRpcError(code=rpc_code, message=message, data=data),
        id=response_id,
    )
    await websocket.send_text(json.dumps(frame.model_dump(mode="json")))


async def _send_ws_asap_error_frame(
    websocket: WebSocket,
    request_handler: "ASAPRequestHandler",
    ctx: Any,
    exc: ASAPError,
    payload_type: str = "",
) -> None:
    """Map an :class:`ASAPError` raised mid-stream to a canonical JSON-RPC error frame."""
    import time as _time

    duration_seconds = _time.perf_counter() - ctx.start_time
    request_handler.record_error_metrics(  # noqa: SLF001
        ctx.metrics, payload_type, type(exc).__name__, duration_seconds
    )
    err_resp = request_handler.build_jsonrpc_error_for_asap_exception(  # noqa: SLF001
        exc, request_id=ctx.request_id, extra_data={"error": str(exc)}
    )
    await _send_ws_response_frame(websocket, err_resp)


async def _send_ws_result_frame(
    websocket: WebSocket, response_envelope: Envelope, response_id: str | int
) -> None:
    """Wrap *response_envelope* in a JSON-RPC result frame and send it over WS."""
    injected = inject_envelope_trace_context(response_envelope)
    frame = JsonRpcResponse(result={"envelope": injected.model_dump(mode="json")}, id=response_id)
    await websocket.send_text(json.dumps(frame.model_dump()))


async def _send_ws_dispatch_failure(
    websocket: WebSocket, envelope_for_ack: Envelope | None, error: Exception
) -> None:
    """Send a ``rejected`` ack frame when WS dispatch raises, if ack is required."""
    if envelope_for_ack is None or not envelope_for_ack.requires_ack or not envelope_for_ack.id:
        return
    reject_frame = _build_ack_notification_frame(
        original_envelope_id=envelope_for_ack.id,
        status="rejected",
        sender=envelope_for_ack.recipient,
        recipient=envelope_for_ack.sender,
        asap_version=envelope_for_ack.asap_version,
        error=str(error),
    )
    await websocket.send_text(reject_frame)


async def _send_internal_error_frame(
    websocket: WebSocket,
    error: Exception,
    *,
    ctx: Any = None,
    payload_type: str = "",
    response_id: str | int | None = None,
) -> WSCloseAction:
    """Emit a sanitized JSON-RPC internal-error frame; ``CLOSE_FATAL`` if send fails.

    Parity with HTTP :meth:`ASAPRequestHandler._handle_internal_error`: the full
    exception is logged server-side, but the client only sees ``str(error)`` in
    debug mode and a generic ``"Internal server error"`` otherwise (no exception
    text leakage in production). Records error metrics when *ctx* is available.
    The frame ``id`` is set to *response_id* when known so clients can correlate
    the failure (CR#5); falls back to ``None`` when the request id is unavailable.
    """
    import time as _time

    if ctx is not None:
        duration_seconds = _time.perf_counter() - ctx.start_time
        # Inline the error-metric recording (parity with record_error_metrics)
        # rather than calling the unbound handler method.
        ctx.metrics.increment_counter(
            "asap_requests_total", {"payload_type": payload_type, "status": "error"}
        )
        ctx.metrics.increment_counter(
            "asap_requests_error_total",
            {"payload_type": payload_type, "error_type": "internal_error"},
        )
        ctx.metrics.observe_histogram(
            "asap_request_duration_seconds",
            duration_seconds,
            {"payload_type": payload_type, "status": "error"},
        )
    logger.warning(
        "asap.websocket.internal_error",
        error=str(error),
        error_type=type(error).__name__,
        exc_info=True,
    )
    error_data: dict[str, Any] = (
        {"error": str(error), "type": type(error).__name__}
        if is_debug_mode()
        else {"error": "Internal server error"}
    )
    error_payload = {
        "jsonrpc": "2.0",
        "error": {
            "code": INTERNAL_ERROR,
            "message": "Internal error",
            "data": error_data,
        },
        "id": response_id,
    }
    try:
        await websocket.send_text(json.dumps(error_payload))
    except Exception as send_error:  # noqa: BLE001 — send failure breaks the loop
        logger.debug("asap.websocket.error_payload_failed", error=str(send_error))
        return WSCloseAction.CLOSE_FATAL
    return WSCloseAction.CONTINUE


__all__ = [
    "_dispatch_ws_envelope",
    "_extract_envelope_for_ack",
    "_maybe_send_received_ack",
    "_registry_has_streaming_for_payload",
    "_send_rate_limit_error",
    "_synthesize_ws_request",
]
