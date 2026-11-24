"""Shared ASAP request parsing, authentication, validation, and dispatch pipeline.

:class:`ASAPRequestHandler` prepares JSON-RPC requests, validates envelopes,
enforces auth, records metrics, and dispatches payloads for HTTP, SSE, and
WebSocket request paths.

Tests patch ``asap.transport.server.logger`` and
``asap.transport.server.is_debug_log_mode``. This module reads those attributes
from ``_server`` at call time; other observability helpers are imported directly.
"""

from __future__ import annotations

import json
import time
import traceback
from collections.abc import AsyncIterator
from typing import Any, TypeVar, Union

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response
from starlette.responses import StreamingResponse
from opentelemetry import context
from pydantic import ValidationError

from asap.errors import (
    ASAPError,
    InvalidNonceError,
    InvalidTimestampError,
    ThreadPoolExhaustedError,
    jsonrpc_error_data_for_asap_exception,
)
from asap.models.constants import MAX_REQUEST_SIZE
from asap.models.entities import Manifest
from asap.models.envelope import Envelope
from asap.observability import get_metrics, is_debug_mode, sanitize_for_logging
from asap.observability.tracing import (
    extract_and_activate_envelope_trace_context,
    inject_envelope_trace_context,
)
from asap.utils.sanitization import sanitize_nonce
from asap.transport.middleware import AuthenticationMiddleware
from asap.observability.metrics import MetricsCollector
from asap.transport.handlers import HandlerNotFoundError
from asap.transport.jsonrpc import (
    ASAP_METHOD,
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    JsonRpcError,
    JsonRpcErrorResponse,
    JsonRpcRequest,
    JsonRpcResponse,
)
from asap.transport import lambda_codec
from asap.transport.lambda_codec import LAMBDA_CONTENT_TYPE
from asap.transport.compression import decompress_payload, get_supported_encodings
from asap.transport.validators import (
    NonceStore,
    validate_envelope_nonce,
    validate_envelope_timestamp,
)
from asap.economics.audit import AuditEntry, AuditStore

from asap.transport import server as _server
from asap.transport.server import PreparedRequest, RequestContext, RegistryHolder


# Type aliases kept alongside the class that defines them.
T = TypeVar("T")
HandlerResult = tuple[T | None, JSONResponse | None]
EnvelopeOrError = Union[JSONResponse, tuple[Envelope, str]]

__all__ = [
    "ASAPRequestHandler",
    "_audit_log_operation",
    "_fallback_context",
    "json_safe_validation_errors",
]


def json_safe_validation_errors(exc: ValidationError) -> list[dict[str, Any]]:
    """Return Pydantic validation errors that are JSON-serializable.

    ``ValidationError.errors()`` may embed non-JSON types in ``ctx`` (e.g. a
    raw ``ValueError`` from a field validator). Putting that into
    ``JSONResponse`` raises during Starlette encoding and the client sees
    JSON-RPC ``-32603`` instead of ``-32602``.

    Uses ``exc.json()`` so ``ctx`` values are stringified while locations and
    messages stay intact.

    Example:
        >>> from pydantic import BaseModel, ValidationError
        >>> class M(BaseModel):
        ...     x: int
        >>> try:
        ...     M(x="bad")
        ... except ValidationError as err:
        ...     errs = json_safe_validation_errors(err)
        >>> isinstance(errs[0]["loc"], list)
        True
    """
    loaded: list[dict[str, Any]] = json.loads(exc.json())
    return loaded


def _fallback_context(start_time: float) -> RequestContext:
    """Build a minimal :class:`RequestContext` for error handling before preparation.

    Used when an exception escapes before ``_prepare_request`` returns a
    :class:`PreparedRequest` (e.g. an unexpected error during parsing). Keeps
    the error path metrics/loggable without ``locals()`` introspection.
    """
    temp_rpc_request = JsonRpcRequest(jsonrpc="2.0", method=ASAP_METHOD, params={}, id="")
    return RequestContext(
        request_id="",
        start_time=start_time,
        metrics=get_metrics(),
        rpc_request=temp_rpc_request,
    )


async def _audit_log_operation(
    app_state: Any,
    operation: str,
    agent_urn: str,
    details: dict[str, Any],
) -> None:
    """Append an audit entry if an audit store is configured.

    Failures are logged but never propagate — audit must not break the
    main request path.
    """
    store: AuditStore | None = getattr(app_state, "audit_store", None)
    if store is None:
        return
    from datetime import datetime as _dt
    from datetime import timezone as _tz

    entry = AuditEntry(
        timestamp=_dt.now(_tz.utc),
        operation=operation,
        agent_urn=agent_urn,
        details=details,
    )
    await store.append(entry)


class ASAPRequestHandler:
    """Handler for processing ASAP protocol requests.

    Encapsulates the logic for:
    - Parsing and validating JSON-RPC requests
    - Authenticating requests based on manifest configuration
    - Validating sender identity
    - Dispatching to registered handlers
    - Building error responses
    - Recording metrics

    This class is instantiated by ``create_app()`` and used to handle
    incoming requests on the /asap endpoint.

    Attributes:
        registry: Handler registry for payload dispatch
        manifest: Agent manifest for context
        auth_middleware: Optional authentication middleware

    Example:
        >>> handler = ASAPRequestHandler(RegistryHolder(registry), manifest, auth_middleware)
        >>> response = await handler.handle_message(request)
    """

    def __init__(
        self,
        registry_holder: RegistryHolder,
        manifest: Manifest,
        auth_middleware: AuthenticationMiddleware | None = None,
        max_request_size: int = MAX_REQUEST_SIZE,
        nonce_store: NonceStore | None = None,
    ) -> None:
        self.registry_holder = registry_holder
        self.manifest = manifest
        self.auth_middleware = auth_middleware
        self.max_request_size = max_request_size
        self.nonce_store = nonce_store

    def _normalize_payload_type_for_metrics(self, payload_type: str) -> str:
        if self.registry_holder.registry.has_handler(payload_type):
            return payload_type
        return "other"

    def build_error_response(
        self,
        code: int,
        data: dict[str, Any] | None = None,
        request_id: str | int | None = None,
    ) -> JSONResponse:
        error_response = JsonRpcErrorResponse(
            error=JsonRpcError.from_code(code, data=data),
            id=request_id,
        )
        return JSONResponse(status_code=200, content=error_response.model_dump())

    def build_jsonrpc_error_for_asap_exception(
        self,
        exc: ASAPError,
        *,
        request_id: str | int | None,
        extra_data: dict[str, Any] | None = None,
    ) -> JSONResponse:
        """JSON-RPC error with ASAP *rpc_code* top-level and recovery hints in *data*."""
        data = jsonrpc_error_data_for_asap_exception(exc)
        if extra_data:
            data = {**data, **extra_data}
        extra_headers: dict[str, str] = {}
        details = data.get("details")
        if isinstance(details, dict):
            wa = details.pop("_www_authenticate_asap", None)
            if isinstance(wa, str):
                extra_headers["WWW-Authenticate"] = wa
        payload = JsonRpcErrorResponse(
            error=JsonRpcError(code=exc.rpc_code, message=exc.message, data=data),
            id=request_id,
        )
        if extra_headers:
            return JSONResponse(
                status_code=200, content=payload.model_dump(), headers=extra_headers
            )
        return JSONResponse(status_code=200, content=payload.model_dump())

    def record_error_metrics(
        self,
        metrics: MetricsCollector,
        payload_type: str,
        error_type: str,
        duration_seconds: float,
    ) -> None:
        # Normalize payload_type to prevent cardinality explosion
        normalized_payload_type = self._normalize_payload_type_for_metrics(payload_type)
        metrics.increment_counter(
            "asap_requests_total",
            {"payload_type": normalized_payload_type, "status": "error"},
        )
        metrics.increment_counter(
            "asap_requests_error_total",
            {"payload_type": normalized_payload_type, "error_type": error_type},
        )
        metrics.observe_histogram(
            "asap_request_duration_seconds",
            duration_seconds,
            {"payload_type": normalized_payload_type, "status": "error"},
        )
        # Specific error counters for observability
        if error_type == "parse_error":
            metrics.increment_counter("asap_parse_errors_total")
        elif error_type == "auth_failed":
            metrics.increment_counter("asap_auth_failures_total")
        elif error_type == "invalid_timestamp":
            metrics.increment_counter("asap_invalid_timestamp_total")
        elif error_type == "invalid_nonce":
            metrics.increment_counter("asap_invalid_nonce_total")
        elif error_type == "sender_mismatch":
            metrics.increment_counter("asap_sender_mismatch_total")
        elif error_type in (
            "invalid_envelope",
            "missing_envelope",
            "invalid_params",
        ):
            metrics.increment_counter("asap_validation_errors_total", {"reason": error_type})

    def _validate_envelope(self, ctx: RequestContext) -> EnvelopeOrError:
        rpc_request = ctx.rpc_request
        if not isinstance(rpc_request.params, dict):
            _server.logger.warning(
                "asap.request.invalid_params_type",
                params_type=type(rpc_request.params).__name__,
            )
            error_response = self.build_error_response(
                INVALID_PARAMS,
                data={
                    "error": "JSON-RPC 'params' must be an object",
                    "received_type": type(rpc_request.params).__name__,
                },
                request_id=ctx.request_id,
            )
            self.record_error_metrics(
                ctx.metrics,
                "unknown",
                "invalid_params",
                time.perf_counter() - ctx.start_time,
            )
            return error_response

        # Extract envelope from params
        envelope_data = rpc_request.params.get("envelope")
        if envelope_data is None:
            _server.logger.warning("asap.request.missing_envelope")
            error_response = self.build_error_response(
                INVALID_PARAMS,
                data={"error": "Missing 'envelope' in params"},
                request_id=ctx.request_id,
            )
            self.record_error_metrics(
                ctx.metrics,
                "unknown",
                "missing_envelope",
                time.perf_counter() - ctx.start_time,
            )
            return error_response

        if not isinstance(envelope_data, dict):
            _server.logger.warning(
                "asap.request.invalid_envelope_type",
                type=type(envelope_data).__name__,
            )
            error_response = self.build_error_response(
                INVALID_PARAMS,
                data={"error": "'envelope' must be a JSON object"},
                request_id=ctx.request_id,
            )
            self.record_error_metrics(
                ctx.metrics,
                "unknown",
                "invalid_envelope",
                time.perf_counter() - ctx.start_time,
            )
            return error_response

        try:
            envelope = Envelope(**envelope_data)
            payload_type = envelope.payload_type
            return envelope, payload_type
        except ValidationError as e:
            validation_errors = json_safe_validation_errors(e)
            log_data: dict[str, Any] = {
                "error": "Invalid envelope structure",
                "validation_errors": validation_errors,
            }
            if not is_debug_mode():
                log_data = sanitize_for_logging(log_data)
            _server.logger.warning("asap.request.invalid_envelope", **log_data)
            duration_seconds = time.perf_counter() - ctx.start_time
            self.record_error_metrics(ctx.metrics, "unknown", "invalid_envelope", duration_seconds)
            return self.build_error_response(
                INVALID_PARAMS,
                data={
                    "error": "Invalid envelope structure",
                    "validation_errors": validation_errors,
                },
                request_id=ctx.request_id,
            )

    async def _dispatch_to_handler(
        self,
        envelope: Envelope,
        ctx: RequestContext,
    ) -> EnvelopeOrError:
        """Dispatch envelope to registered handler.

        Looks up and executes the handler for the envelope's payload type.
        Handles HandlerNotFoundError and converts it to JSON-RPC error response.

        Args:
            envelope: Validated ASAP envelope
            ctx: Request context with rpc_request, start_time, and metrics

        Returns:
            ``(envelope, payload_type)``, a :class:`JSONResponse` (503), or a JSON-RPC error
            response for handler-not-found.
        """
        payload_type = envelope.payload_type
        try:
            response_envelope = await self.registry_holder.registry.dispatch_async(
                envelope, self.manifest
            )
            return response_envelope, payload_type
        except ThreadPoolExhaustedError as e:
            # Thread pool exhausted - service temporarily unavailable
            _server.logger.warning(
                "asap.request.thread_pool_exhausted",
                payload_type=payload_type,
                envelope_id=envelope.id,
                max_threads=e.max_threads,
                active_threads=e.active_threads,
            )
            # Record error metric
            duration_seconds = time.perf_counter() - ctx.start_time
            self.record_error_metrics(
                ctx.metrics, payload_type, "thread_pool_exhausted", duration_seconds
            )
            # Return HTTP 503 Service Unavailable (not JSON-RPC error)
            return JSONResponse(
                status_code=503,
                content={
                    "error": "Service Temporarily Unavailable",
                    "code": e.code,
                    "rpc_code": e.rpc_code,
                    "message": e.message,
                    "details": e.details,
                    "recoverable": True,
                    "retry_after_ms": e.retry_after_ms,
                    "fallback_action": e.fallback_action,
                },
            )
        except HandlerNotFoundError as e:
            # No handler registered for this payload type
            _server.logger.warning(
                "asap.request.handler_not_found",
                payload_type=e.payload_type,
                envelope_id=envelope.id,
            )
            # Record error metric
            duration_seconds = time.perf_counter() - ctx.start_time
            self.record_error_metrics(
                ctx.metrics, payload_type, "handler_not_found", duration_seconds
            )
            return self.build_jsonrpc_error_for_asap_exception(
                e,
                request_id=ctx.request_id,
                extra_data={"error": str(e)},
            )

    async def _authenticate_request(
        self,
        request: Request,
        ctx: RequestContext,
    ) -> HandlerResult[str]:
        if self.auth_middleware is None:
            return None, None

        try:
            authenticated_agent_id = await self.auth_middleware.verify_authentication(request)
            return authenticated_agent_id, None
        except HTTPException as e:
            # Authentication failed - return JSON-RPC error
            _server.logger.warning(
                "asap.request.auth_failed",
                status_code=e.status_code,
                detail=e.detail,
            )
            # Map HTTP status to JSON-RPC error code
            error_code = INVALID_REQUEST if e.status_code == 401 else INVALID_PARAMS
            error_response = self.build_error_response(
                error_code,
                data={"error": str(e.detail), "status_code": e.status_code},
                request_id=ctx.request_id,
            )
            self.record_error_metrics(
                ctx.metrics,
                "unknown",
                "auth_failed",
                time.perf_counter() - ctx.start_time,
            )
            return None, error_response

    def _verify_sender_matches_auth(
        self,
        authenticated_agent_id: str | None,
        envelope: Envelope,
        ctx: RequestContext,
        payload_type: str,
    ) -> JSONResponse | None:
        if self.auth_middleware is None:
            return None

        try:
            self.auth_middleware.verify_sender_matches_auth(authenticated_agent_id, envelope.sender)
            return None
        except HTTPException as e:
            # Sender mismatch - return JSON-RPC error
            _server.logger.warning(
                "asap.request.sender_mismatch",
                authenticated_agent=authenticated_agent_id,
                envelope_sender=envelope.sender,
            )
            error_response = self.build_error_response(
                INVALID_PARAMS,
                data={"error": str(e.detail), "status_code": e.status_code},
                request_id=ctx.request_id,
            )
            duration_seconds = time.perf_counter() - ctx.start_time
            self.record_error_metrics(
                ctx.metrics, payload_type, "sender_mismatch", duration_seconds
            )
            return error_response

    def _build_success_response(
        self,
        response_envelope: Envelope,
        ctx: RequestContext,
        payload_type: str,
        accept_lambda: bool = False,
    ) -> Response:
        response_envelope = inject_envelope_trace_context(response_envelope)
        duration_seconds = time.perf_counter() - ctx.start_time
        duration_ms = duration_seconds * 1000

        # Normalize payload_type to prevent cardinality explosion
        normalized_payload_type = self._normalize_payload_type_for_metrics(payload_type)

        # Record success metrics
        ctx.metrics.increment_counter(
            "asap_requests_total",
            {"payload_type": normalized_payload_type, "status": "success"},
        )
        ctx.metrics.increment_counter(
            "asap_requests_success_total",
            {"payload_type": normalized_payload_type},
        )
        ctx.metrics.observe_histogram(
            "asap_request_duration_seconds",
            duration_seconds,
            {"payload_type": normalized_payload_type, "status": "success"},
        )

        # Log successful processing
        _server.logger.info(
            "asap.request.processed",
            envelope_id=response_envelope.id,
            response_id=response_envelope.id,
            trace_id=response_envelope.trace_id,
            payload_type=payload_type,
            duration_ms=round(duration_ms, 2),
        )

        # Wrap response in JSON-RPC
        # JsonRpcResponse requires id to be str | int, not None
        response_id: str | int = ctx.request_id if ctx.request_id is not None else ""
        rpc_response = JsonRpcResponse(
            result={"envelope": response_envelope.model_dump(mode="json")},
            id=response_id,
        )

        if accept_lambda and lambda_codec.is_available():
            try:
                encoded_body = lambda_codec.encode(rpc_response.model_dump_json(by_alias=True))
                _server.logger.debug(
                    "asap.server.lambda_response",
                    envelope_id=response_envelope.id,
                    encoded_size=len(encoded_body),
                )
                return Response(
                    status_code=200,
                    content=encoded_body,
                    media_type=LAMBDA_CONTENT_TYPE,
                )
            except Exception as e:
                _server.logger.warning(
                    "asap.server.lambda_encode_failed",
                    error=str(e),
                    error_type=type(e).__name__,
                    exc_info=True,
                )
                # Fall through to JSON response

        return JSONResponse(
            status_code=200,
            content=rpc_response.model_dump(),
        )

    def _handle_internal_error(
        self,
        error: Exception,
        ctx: RequestContext,
        payload_type: str,
    ) -> JSONResponse:
        duration_seconds = time.perf_counter() - ctx.start_time
        duration_ms = duration_seconds * 1000

        # Record error metrics (normalized to prevent cardinality explosion)
        self.record_error_metrics(ctx.metrics, payload_type, "internal_error", duration_seconds)

        # Always log full error server-side for diagnostics
        _server.logger.exception(
            "asap.request.error",
            error=str(error),
            error_type=type(error).__name__,
            duration_ms=round(duration_ms, 2),
        )

        # Production: generic error to client; debug: full error and stack trace
        if is_debug_mode():
            error_data: dict[str, Any] = {
                "error": str(error),
                "type": type(error).__name__,
                "traceback": traceback.format_exc(),
            }
        else:
            error_data = {"error": "Internal server error"}

        internal_error = JsonRpcErrorResponse(
            error=JsonRpcError.from_code(INTERNAL_ERROR, data=error_data),
            id=ctx.request_id,
        )
        return JSONResponse(
            status_code=200,
            content=internal_error.model_dump(),
        )

    def _log_request_debug(self, rpc_request: JsonRpcRequest) -> None:
        """Log full JSON-RPC request when ASAP_DEBUG_LOG is enabled (structured JSON)."""
        if not _server.is_debug_log_mode():
            return
        request_dict: dict[str, Any] = rpc_request.model_dump()
        if not is_debug_mode():
            request_dict = sanitize_for_logging(request_dict)
        _server.logger.info("asap.request.debug_request", request_json=request_dict)

    def _log_response_debug(self, response: Response) -> None:
        """Log full response when ASAP_DEBUG_LOG is enabled (structured JSON)."""
        if not _server.is_debug_log_mode():
            return
        try:
            body_bytes = response.body
            # Handle both bytes and memoryview
            if isinstance(body_bytes, memoryview):
                body_bytes = body_bytes.tobytes()
            response_dict: dict[str, Any] = json.loads(body_bytes.decode("utf-8"))
        except (ValueError, AttributeError):
            response_dict = {"_raw": "(unable to decode response body)"}
        if not is_debug_mode():
            response_dict = sanitize_for_logging(response_dict)
        _server.logger.info(
            "asap.request.debug_response",
            status_code=response.status_code,
            response_json=response_dict,
        )

    async def _parse_and_validate_request(
        self,
        request: Request,
    ) -> HandlerResult[JsonRpcRequest]:
        # Parse JSON body
        try:
            body = await self.parse_json_body(request)
        except HTTPException as e:
            # HTTPException (e.g., 413 Payload Too Large) should be returned directly
            # Don't convert to JSON-RPC error response
            return None, JSONResponse(
                status_code=e.status_code,
                content={"detail": e.detail},
                headers=e.headers if hasattr(e, "headers") else None,
            )
        except ValueError as e:
            # Invalid JSON - return parse error
            error_response = self.build_error_response(
                PARSE_ERROR,
                data={"error": str(e)},
                request_id=None,
            )
            # Create temporary context for metrics (before we have rpc_request)
            temp_metrics = get_metrics()
            self.record_error_metrics(temp_metrics, "unknown", "parse_error", 0.0)
            return None, error_response

        if not isinstance(body, dict):
            error_response = self.build_error_response(
                INVALID_REQUEST,
                data={
                    "error": "JSON-RPC request must be an object",
                    "received_type": type(body).__name__,
                },
                request_id=None,
            )
            temp_metrics = get_metrics()
            self.record_error_metrics(temp_metrics, "unknown", "invalid_request", 0.0)
            return None, error_response

        rpc_request, validation_error = self.validate_jsonrpc_request(body)
        if validation_error is not None:
            temp_metrics = get_metrics()
            self.record_error_metrics(temp_metrics, "unknown", "invalid_request", 0.0)
            return None, validation_error

        if rpc_request is None:
            error_response = self.build_error_response(
                INTERNAL_ERROR,
                data={"error": "Internal validation error"},
                request_id=None,
            )
            temp_metrics = get_metrics()
            self.record_error_metrics(
                temp_metrics, "unknown", "internal_error", time.perf_counter()
            )
            return None, error_response

        return rpc_request, None

    def _validate_request_size(self, request: Request, max_size: int) -> None:
        """Validate that request size does not exceed maximum.

        Checks Content-Length header first, then validates actual body size
        if available. Raises HTTPException(413) if request is too large.

        Args:
            request: FastAPI request object
            max_size: Maximum allowed request size in bytes

        Raises:
            HTTPException: If request size exceeds maximum (413 Payload Too Large)
        """
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                size = int(content_length)
                if size > max_size:
                    _server.logger.warning(
                        "asap.request.size_exceeded",
                        content_length=size,
                        max_size=max_size,
                    )
                    raise HTTPException(
                        status_code=413,
                        detail=f"Request size ({size} bytes) exceeds maximum ({max_size} bytes)",
                    )
            except ValueError:
                _server.logger.debug(
                    "asap.request.invalid_content_length", content_length=content_length
                )

    async def parse_json_body(self, request: Request) -> dict[str, Any]:
        """Parse JSON body from request with size validation and decompression.

        Validates request size before parsing to prevent DoS attacks.
        Checks both Content-Length header and actual body size.
        Automatically decompresses gzip/brotli encoded requests.

        Args:
            request: FastAPI request object

        Returns:
            Parsed JSON body

        Raises:
            HTTPException: If request size exceeds maximum (413)
            HTTPException: If Content-Encoding is unsupported (415)
            ValueError: If JSON is invalid
        """
        content_encoding = request.headers.get("content-encoding", "").lower().strip()
        supported_encodings = get_supported_encodings() + ["identity", ""]
        if content_encoding and content_encoding not in supported_encodings:
            _server.logger.warning(
                "asap.request.unsupported_encoding",
                content_encoding=content_encoding,
                supported=supported_encodings,
            )
            raise HTTPException(
                status_code=415,
                detail=f"Unsupported Content-Encoding: {content_encoding}. Supported: {', '.join(get_supported_encodings())}",
            )

        try:
            body_bytes = bytearray()
            async for chunk in request.stream():
                body_bytes.extend(chunk)
                if len(body_bytes) > self.max_request_size:
                    _server.logger.warning(
                        "asap.request.size_exceeded",
                        actual_size=len(body_bytes),
                        max_size=self.max_request_size,
                    )
                    raise HTTPException(
                        status_code=413,
                        detail=f"Request size ({len(body_bytes)} bytes) exceeds maximum ({self.max_request_size} bytes)",
                    )

            # Decompress if Content-Encoding is specified
            if content_encoding and content_encoding not in ("identity", ""):
                try:
                    compressed_size = len(body_bytes)
                    body_bytes = bytearray(decompress_payload(bytes(body_bytes), content_encoding))
                    decompressed_size = len(body_bytes)

                    _server.logger.debug(
                        "asap.request.decompressed",
                        content_encoding=content_encoding,
                        compressed_size=compressed_size,
                        decompressed_size=decompressed_size,
                    )

                    if decompressed_size > self.max_request_size:
                        compression_ratio = (
                            decompressed_size / compressed_size if compressed_size > 0 else 0
                        )
                        _server.logger.warning(
                            "asap.request.decompressed_size_exceeded",
                            decompressed_size=decompressed_size,
                            original_compressed_size=compressed_size,
                            compression_ratio=round(compression_ratio, 2),
                            max_size=self.max_request_size,
                        )
                        raise HTTPException(
                            status_code=413,
                            detail=f"Decompressed request size ({decompressed_size} bytes) exceeds maximum ({self.max_request_size} bytes)",
                        )
                except ValueError as e:
                    # Decompression failed (invalid compressed data or unsupported encoding)
                    _server.logger.warning(
                        "asap.request.decompression_failed",
                        content_encoding=content_encoding,
                        error=str(e),
                    )
                    raise HTTPException(
                        status_code=400,
                        detail=f"Failed to decompress request: {e}",
                    ) from e
                except (OSError, EOFError) as e:
                    # Invalid gzip/brotli data (OSError) or truncated data (EOFError)
                    _server.logger.warning(
                        "asap.request.invalid_compressed_data",
                        content_encoding=content_encoding,
                        error=str(e),
                    )
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid compressed data: {e}",
                    ) from e

            # Parse JSON from bytes
            body: dict[str, Any] = json.loads(body_bytes.decode("utf-8"))
            return body
        except UnicodeDecodeError as e:
            _server.logger.warning("asap.request.invalid_encoding", error=str(e))
            raise ValueError(f"Invalid UTF-8 encoding: {e}") from e
        except (json.JSONDecodeError, RecursionError) as e:
            _server.logger.warning("asap.request.invalid_json", error=str(e))
            raise ValueError(f"Invalid JSON: {e}") from e

    def validate_jsonrpc_request(
        self, body: dict[str, Any]
    ) -> tuple[JsonRpcRequest | None, JSONResponse | None]:
        """Validate JSON-RPC request structure and method.

        Args:
            body: Parsed JSON body

        Returns:
            Tuple of (JsonRpcRequest, None) if valid, or (None, error_response) if invalid
        """
        try:
            rpc_request = JsonRpcRequest(**body)
        except (ValidationError, TypeError) as e:
            error_code = INVALID_REQUEST
            error_message = "Invalid JSON-RPC structure"
            if isinstance(e, ValidationError):
                validation_errors = json_safe_validation_errors(e)
                # If params validation failed with dict_type error, use INVALID_PARAMS
                for error in validation_errors:
                    if error.get("loc") == ["params"] and error.get("type") == "dict_type":
                        error_code = INVALID_PARAMS
                        error_message = "JSON-RPC 'params' must be an object"
                        break
            else:
                validation_errors = [
                    {"type": "type_error", "loc": [], "msg": str(e), "input": None}
                ]

            log_struct: dict[str, Any] = {
                "error": error_message,
                "error_type": type(e).__name__,
                "validation_errors": validation_errors,
            }
            if not is_debug_mode():
                log_struct = sanitize_for_logging(log_struct)
            _server.logger.warning("asap.request.invalid_structure", **log_struct)
            error_response = self.build_error_response(
                error_code,
                data={
                    "error": error_message,
                    "validation_errors": validation_errors,
                },
                request_id=body.get("id") if isinstance(body, dict) else None,
            )
            return None, error_response

        if rpc_request.method != ASAP_METHOD:
            _server.logger.warning("asap.request.unknown_method", method=rpc_request.method)
            error_response = self.build_error_response(
                METHOD_NOT_FOUND,
                data={"method": rpc_request.method},
                request_id=rpc_request.id,
            )
            return None, error_response

        return rpc_request, None

    async def handle_message(self, request: Request) -> Response:
        """Handle ASAP messages wrapped in JSON-RPC 2.0.

        This method:
        1. Receives JSON-RPC wrapped ASAP envelopes
        2. Validates the request structure
        3. Extracts and processes the ASAP envelope
        4. Returns response wrapped in JSON-RPC
        5. Records metrics for observability

        Args:
            request: FastAPI request object with JSON body

        Returns:
            JSON-RPC response or error response

        Example:
            >>> response = await handler.handle_message(request)
        """
        start_time = time.perf_counter()
        payload_type = "unknown"
        ctx: RequestContext | None = None

        try:
            prepared = await self._prepare_request(request, start_time)
            if isinstance(prepared, Response):
                self._log_response_debug(prepared)
                return prepared
            ctx = prepared.ctx
            envelope = prepared.envelope
            payload_type = envelope.payload_type

            try:
                dispatch_result = await self._dispatch_to_handler(envelope, ctx)
                if isinstance(dispatch_result, JSONResponse):
                    self._log_response_debug(dispatch_result)
                    return dispatch_result
                response_envelope, payload_type = dispatch_result

                accept_header = request.headers.get("accept", "")
                accept_lambda = LAMBDA_CONTENT_TYPE in accept_header

                try:
                    await _audit_log_operation(
                        request.app.state,
                        operation=payload_type,
                        agent_urn=envelope.sender,
                        details={
                            "envelope_id": envelope.id,
                            "response_id": response_envelope.id,
                        },
                    )
                except Exception:
                    _server.logger.warning("asap.audit.log_failed", exc_info=True)

                success_resp = self._build_success_response(
                    response_envelope, ctx, payload_type, accept_lambda=accept_lambda
                )
                self._log_response_debug(success_resp)
                return success_resp
            finally:
                self._detach_trace(prepared.trace_token)

        except ASAPError as e:
            # Preserve protocol-level errors raised by handlers as JSON-RPC ASAP errors.
            error_ctx = ctx if ctx is not None else _fallback_context(start_time)
            duration_seconds = time.perf_counter() - error_ctx.start_time
            self.record_error_metrics(
                error_ctx.metrics, payload_type, type(e).__name__, duration_seconds
            )
            _server.logger.warning(
                "asap.request.protocol_error",
                error=str(e),
                error_type=type(e).__name__,
                duration_ms=round(duration_seconds * 1000, 2),
                exc_info=True,
            )
            err_resp = self.build_jsonrpc_error_for_asap_exception(
                e,
                request_id=error_ctx.request_id,
                extra_data={"error": str(e)},
            )
            self._log_response_debug(err_resp)
            return err_resp
        except Exception as e:
            error_ctx = ctx if ctx is not None else _fallback_context(start_time)
            err_resp = self._handle_internal_error(e, error_ctx, payload_type)
            self._log_response_debug(err_resp)
            return err_resp

    async def _prepare_request(
        self,
        request: Request,
        start_time: float,
        *,
        received_log_event: str = "asap.request.received",
    ) -> PreparedRequest | Response:
        """Run the shared request-preparation pipeline.

        Executes the parse → auth → envelope → trace → sender → timestamp →
        nonce gate used by :meth:`handle_message`, :meth:`_prepare_streaming_request`
        and :meth:`iter_websocket_stream`. On success returns a
        :class:`PreparedRequest` (the caller owns detaching ``trace_token``).
        On any preparation failure returns an error :class:`Response` and
        detaches the trace token itself.

        Args:
            request: FastAPI request object with a JSON-RPC body
            start_time: Request start time (``time.perf_counter()``) for metrics
            received_log_event: Structured log event name emitted once the
                envelope is accepted (``asap.request.received`` for the HTTP
                request path, ``asap.request.stream_received`` for streaming).

        Returns:
            ``PreparedRequest`` on success, or a JSON-RPC error ``Response``.
        """
        metrics = get_metrics()

        parse_result = await self._parse_and_validate_request(request)
        rpc_request, parse_error = parse_result
        if parse_error is not None:
            self._log_response_debug(parse_error)
            return parse_error
        if rpc_request is None:
            raise RuntimeError("Internal error: rpc_request is None after validation")

        self._log_request_debug(rpc_request)

        ctx = RequestContext(
            request_id=rpc_request.id,
            start_time=start_time,
            metrics=metrics,
            rpc_request=rpc_request,
        )

        auth_result = await self._authenticate_request(request, ctx)
        authenticated_agent_id, auth_error = auth_result
        if auth_error is not None:
            self._log_response_debug(auth_error)
            return auth_error

        envelope_result = self._validate_envelope(ctx)
        if isinstance(envelope_result, JSONResponse):
            self._log_response_debug(envelope_result)
            return envelope_result
        envelope, payload_type = envelope_result

        trace_token = extract_and_activate_envelope_trace_context(envelope)
        sender_error = self._verify_sender_matches_auth(
            authenticated_agent_id, envelope, ctx, payload_type
        )
        if sender_error is not None:
            self._log_response_debug(sender_error)
            self._detach_trace(trace_token)
            return sender_error

        timestamp_error = self._validate_timestamp(envelope, ctx, payload_type)
        if timestamp_error is not None:
            self._log_response_debug(timestamp_error)
            self._detach_trace(trace_token)
            return timestamp_error

        nonce_error = self._validate_nonce(envelope, ctx, payload_type)
        if nonce_error is not None:
            self._log_response_debug(nonce_error)
            self._detach_trace(trace_token)
            return nonce_error

        _server.logger.info(
            received_log_event,
            envelope_id=envelope.id,
            trace_id=envelope.trace_id,
            payload_type=envelope.payload_type,
            sender=envelope.sender,
            recipient=envelope.recipient,
            authenticated=authenticated_agent_id is not None,
        )

        return PreparedRequest(
            ctx=ctx,
            envelope=envelope,
            authenticated_agent_id=authenticated_agent_id,
            trace_token=trace_token,
        )

    def _detach_trace(self, trace_token: Any) -> None:
        """Detach the OpenTelemetry trace token if it was activated."""
        if trace_token is not None:
            context.detach(trace_token)

    def _validate_timestamp(
        self,
        envelope: Envelope,
        ctx: RequestContext,
        payload_type: str,
    ) -> JSONResponse | None:
        """Validate the envelope timestamp; return a JSON-RPC error on failure."""
        try:
            validate_envelope_timestamp(envelope)
        except InvalidTimestampError as e:
            log_ts: dict[str, Any] = {
                "envelope_id": envelope.id,
                "error": e.message,
                "details": e.details,
            }
            if not is_debug_mode() and isinstance(e.details, dict):
                log_ts["details"] = sanitize_for_logging(e.details)
            _server.logger.warning("asap.request.invalid_timestamp", **log_ts)
            duration_seconds = time.perf_counter() - ctx.start_time
            self.record_error_metrics(
                ctx.metrics, payload_type, "invalid_timestamp", duration_seconds
            )
            return self.build_jsonrpc_error_for_asap_exception(
                e,
                request_id=ctx.request_id,
                extra_data={"error": "Invalid envelope timestamp"},
            )
        return None

    def _validate_nonce(
        self,
        envelope: Envelope,
        ctx: RequestContext,
        payload_type: str,
    ) -> JSONResponse | None:
        """Validate the envelope nonce; return a JSON-RPC error on failure."""
        try:
            validate_envelope_nonce(envelope, self.nonce_store)
        except InvalidNonceError as e:
            nonce_sanitized = sanitize_nonce(e.nonce)
            error_msg = e.message if is_debug_mode() else "Duplicate nonce detected"
            _server.logger.warning(
                "asap.request.invalid_nonce",
                envelope_id=envelope.id,
                nonce=nonce_sanitized,
                error=error_msg,
            )
            duration_seconds = time.perf_counter() - ctx.start_time
            self.record_error_metrics(ctx.metrics, payload_type, "invalid_nonce", duration_seconds)
            return self.build_jsonrpc_error_for_asap_exception(
                e,
                request_id=ctx.request_id,
                extra_data={"error": "Invalid envelope nonce"},
            )
        return None

    async def _prepare_streaming_request(
        self,
        request: Request,
        start_time: float,
    ) -> tuple[RequestContext, Envelope, Any | None] | Response:
        """Parse JSON-RPC, auth, nonce/timestamp, ensure a streaming handler exists.

        Returns ``(context, envelope, trace_token)`` on success, or an error
        ``Response`` (caller must not start a stream).
        """
        prepared = await self._prepare_request(
            request, start_time, received_log_event="asap.request.stream_received"
        )
        if isinstance(prepared, Response):
            return prepared
        ctx = prepared.ctx
        envelope = prepared.envelope
        trace_token = prepared.trace_token

        if not self.registry_holder.registry.has_streaming_handler(envelope.payload_type):
            duration_seconds = time.perf_counter() - ctx.start_time
            self.record_error_metrics(
                ctx.metrics, envelope.payload_type, "handler_not_found", duration_seconds
            )
            err_resp = self.build_jsonrpc_error_for_asap_exception(
                HandlerNotFoundError(envelope.payload_type),
                request_id=ctx.request_id,
                extra_data={"error": "No streaming handler registered for payload type"},
            )
            self._log_response_debug(err_resp)
            self._detach_trace(trace_token)
            return err_resp

        return ctx, envelope, trace_token

    async def handle_stream(self, request: Request) -> Response:
        """Handle ASAP streaming: JSON-RPC body, SSE ``text/event-stream`` response.

        Each event body is one ``Envelope`` (typically ``TaskStream``) as JSON on a ``data:`` line.
        """
        start_time = time.perf_counter()
        payload_type = "unknown"
        ctx: RequestContext | None = None

        try:
            prepared = await self._prepare_streaming_request(request, start_time)
            if isinstance(prepared, Response):
                return prepared
            ctx, envelope, trace_token = prepared
            payload_type = envelope.payload_type

            async def sse_events() -> AsyncIterator[bytes]:
                try:
                    async for (
                        response_envelope
                    ) in self.registry_holder.registry.dispatch_stream_async(
                        envelope, self.manifest
                    ):
                        injected = inject_envelope_trace_context(response_envelope)
                        line = f"data: {json.dumps(injected.model_dump(mode='json'))}\n\n"
                        yield line.encode("utf-8")
                    duration_seconds = time.perf_counter() - ctx.start_time
                    normalized_payload_type = self._normalize_payload_type_for_metrics(payload_type)
                    ctx.metrics.increment_counter(
                        "asap_requests_total",
                        {"payload_type": normalized_payload_type, "status": "success"},
                    )
                    ctx.metrics.increment_counter(
                        "asap_requests_success_total",
                        {"payload_type": normalized_payload_type},
                    )
                    ctx.metrics.observe_histogram(
                        "asap_request_duration_seconds",
                        duration_seconds,
                        {"payload_type": normalized_payload_type, "status": "success"},
                    )
                    _server.logger.info(
                        "asap.request.stream_processed",
                        envelope_id=envelope.id,
                        payload_type=payload_type,
                        duration_ms=round(duration_seconds * 1000, 2),
                    )
                finally:
                    if trace_token is not None:
                        context.detach(trace_token)

            return StreamingResponse(
                sse_events(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                },
            )
        except Exception as e:
            error_ctx = ctx if ctx is not None else _fallback_context(start_time)
            err_resp = self._handle_internal_error(e, error_ctx, payload_type)
            self._log_response_debug(err_resp)
            return err_resp
