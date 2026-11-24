"""Send path for :class:`asap.transport.client.ASAPClient`.

This mixin owns ``send`` orchestration, response status handling, retry/backoff
behavior, WebSocket forwarding, compression, streaming, and send-path metrics.
It is mixed into ``ASAPClient`` and is not intended to be instantiated directly.

The mixin declares the subset of ``ASAPClient`` attributes it relies on as
class-level annotations so static type-checking sees them without importing
the concrete ``ASAPClient`` (which would create a circular import).
"""

from __future__ import annotations

import asyncio
import itertools
import json
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import httpx

from asap.errors import (
    ASAPConnectionError,
    ASAPRemoteError,
    ASAPTimeoutError,
    CircuitOpenError,
    RemoteRPCError,
    remote_rpc_error_from_json,
)
from asap.models.constants import ASAP_VERSION_HEADER
from asap.models.envelope import Envelope
from asap.models.ids import generate_id
from asap.observability import get_metrics
from asap.transport.circuit_breaker import CircuitBreaker
from asap.transport.client._helpers import (
    _RetryableSend,
    _log_circuit_event,
    _parse_retry_after,
    _record_send_error_metrics,
    logger,
)
from asap.transport import lambda_codec
from asap.transport.lambda_codec import LAMBDA_CONTENT_TYPE
from asap.transport.compression import (
    CompressionAlgorithm,
    compress_payload,
    get_accept_encoding_header,
)
from asap.transport.errors import (
    ProtocolCorrelationError,
    assert_correlation_binds,
    assert_stream_correlation_binds,
)
from asap.transport.jsonrpc import ASAP_METHOD
from asap.transport.ws import WebSocketRemoteError
from asap.utils.sanitization import sanitize_url

if TYPE_CHECKING:
    from asap.transport.ws import WebSocketTransport


class _SendMixin:
    """``send`` orchestration + response/retry handlers for ``ASAPClient``."""

    # --- Shared ASAPClient attributes relied on by the send path --------------
    base_url: str
    timeout: float
    max_retries: int
    _client: httpx.AsyncClient | None
    _ws_transport: "WebSocketTransport | None"
    _circuit_breaker: CircuitBreaker | None
    _request_counter: "itertools.count[int]"
    _http2: bool
    _compression: bool
    _compression_threshold: int
    _lambda_codec_enabled: bool
    _auth_token: str | None
    _asap_version_header_value: str
    # --------------------------------------------------------------------------

    def _calculate_backoff(self, attempt: int) -> float:
        """Calculate exponential backoff with optional jitter (defined on ``ASAPClient``)."""
        raise NotImplementedError  # pragma: no cover - provided by ASAPClient

    def _capture_asap_version_header(self, response: httpx.Response) -> None:
        """Store ``ASAP-Version`` from an HTTP response (defined on ``ASAPClient``)."""
        raise NotImplementedError  # pragma: no cover - provided by ASAPClient

    async def _ingest_asap_challenge_401(self, response: httpx.Response) -> None:
        """Record ASAP discovery from a 401 (defined on ``ASAPClient``)."""
        raise NotImplementedError  # pragma: no cover - provided by ASAPClient

    async def send(self, envelope: Envelope) -> Envelope:
        """Send an envelope to the remote agent and receive response.

        Wraps the envelope in a JSON-RPC 2.0 request, sends it to the
        remote agent's /asap endpoint, and unwraps the response.

        Args:
            envelope: ASAP envelope to send

        Returns:
            Response envelope from the remote agent

        Raises:
            ValueError: If envelope is None
            ASAPConnectionError: If connection fails or HTTP error occurs
            ASAPTimeoutError: If request times out
            ASAPRemoteError: If remote agent returns JSON-RPC error
            CircuitOpenError: If circuit breaker is open and request is rejected

        Example:
            >>> async with ASAPClient("http://localhost:8000") as client:
            ...     response = await client.send(envelope)
            ...     response.payload_type
        """
        if envelope is None:
            raise ValueError("envelope cannot be None")

        if not self._client and not self._ws_transport:
            raise ASAPConnectionError(
                "Client not connected. Use 'async with' context.",
                url=sanitize_url(self.base_url),
            )

        if self._ws_transport:
            return await self._send_websocket(envelope)

        if self._circuit_breaker is not None and not self._circuit_breaker.can_attempt():
            raise CircuitOpenError(
                base_url=sanitize_url(self.base_url),
                consecutive_failures=self._circuit_breaker.get_consecutive_failures(),
            )

        start_time = time.perf_counter()
        sanitized_url = sanitize_url(self.base_url)
        idempotency_key = generate_id()
        request_id = f"req-{next(self._request_counter)}"
        logger.info(
            "asap.client.send",
            target_url=sanitized_url,
            envelope_id=envelope.id,
            trace_id=envelope.trace_id,
            payload_type=envelope.payload_type,
            idempotency_key=idempotency_key,
            max_retries=self.max_retries,
            message=(
                f"Sending envelope {envelope.id} to {sanitized_url} "
                f"(payload: {envelope.payload_type}, max_retries: {self.max_retries})"
            ),
        )

        json_rpc_request = {
            "jsonrpc": "2.0",
            "method": ASAP_METHOD,
            "params": {
                "envelope": envelope.model_dump(mode="json"),
                "idempotency_key": idempotency_key,
            },
            "id": request_id,
        }
        request_body = json.dumps(json_rpc_request).encode("utf-8")
        request_body, content_encoding = self._compress_request_body(
            request_body, json_rpc_request, envelope_id=envelope.id
        )

        last_exception: Exception | None = None
        for attempt in range(self.max_retries):
            if attempt > 0:
                get_metrics().increment_counter("asap_transport_retries_total")
            try:
                headers = self._build_send_headers(idempotency_key, content_encoding)
                if self._client is None:
                    raise RuntimeError(
                        "ASAPClient must be used as an async context manager before sending"
                    )
                response = await self._client.post(
                    f"{self.base_url}/asap",
                    headers=headers,
                    content=request_body,
                )
                self._capture_asap_version_header(response)
                if response.status_code == 401:
                    await self._ingest_asap_challenge_401(response)
                if self._http2 and response.http_version != "HTTP/2":
                    logger.debug(
                        "asap.client.http_fallback",
                        target_url=sanitized_url,
                        requested="HTTP/2",
                        actual=response.http_version,
                        message=f"HTTP/2 requested but used {response.http_version}",
                    )

                return await self._handle_send_response(
                    response, envelope, attempt, start_time, sanitized_url
                )

            except _RetryableSend as retry:
                last_exception = retry.exc
                continue

            except (httpx.ConnectError, httpx.TimeoutException) as e:
                last_exception = await self._handle_send_transport_exception(
                    e, envelope, attempt, start_time, sanitized_url
                )
                continue

            except (
                ASAPConnectionError,
                ASAPRemoteError,
                RemoteRPCError,
                ASAPTimeoutError,
                ProtocolCorrelationError,
            ):
                # Re-raise our custom errors without recording failure again
                # (failures are already recorded before these exceptions are raised)
                raise

            except Exception as e:
                # Unexpected error: record failure, log, wrap in ASAPConnectionError.
                if self._circuit_breaker is not None:
                    self._circuit_breaker.record_failure()
                    _log_circuit_event(self._circuit_breaker, base_url=sanitized_url, opened=True)
                duration_ms = (time.perf_counter() - start_time) * 1000
                logger.exception(
                    "asap.client.error",
                    target_url=sanitized_url,
                    envelope_id=envelope.id,
                    error=str(e),
                    error_type=type(e).__name__,
                    duration_ms=round(duration_ms, 2),
                )
                _record_send_error_metrics(start_time, e)
                raise ASAPConnectionError(
                    f"Unexpected error connecting to {self.base_url}: {e}. "
                    f"Verify the agent is running and accessible.",
                    cause=e,
                    url=sanitize_url(self.base_url),
                ) from e

        if last_exception:  # pragma: no cover
            _record_send_error_metrics(start_time, last_exception)
            raise last_exception
        raise ASAPConnectionError(
            f"Max retries ({self.max_retries}) exceeded for {self.base_url}. "
            f"Verify the agent is running and accessible.",
            url=sanitize_url(self.base_url),
        )  # pragma: no cover

    async def _send_websocket(self, envelope: Envelope) -> Envelope:
        """Dispatch ``send`` over the active WebSocket transport."""
        assert self._ws_transport is not None
        if self._circuit_breaker is not None and not self._circuit_breaker.can_attempt():
            raise CircuitOpenError(
                base_url=sanitize_url(self.base_url),
                consecutive_failures=self._circuit_breaker.get_consecutive_failures(),
            )
        try:
            return await self._ws_transport.send_and_receive(envelope)
        except WebSocketRemoteError as e:
            raise remote_rpc_error_from_json(e.code, e.message, e.data) from e
        except asyncio.TimeoutError as e:
            raise ASAPTimeoutError(
                f"WebSocket receive timed out after {self.timeout}s",
                timeout=self.timeout,
            ) from e

    def _compress_request_body(
        self,
        request_body: bytes,
        json_rpc_request: dict[str, Any],
        *,
        envelope_id: str | None,
    ) -> tuple[bytes, str | None]:
        """Apply compression if enabled and above threshold.

        Returns the (possibly compressed) body and the ``Content-Encoding`` value
        (``None`` when no compression was applied).
        """
        if not self._compression:
            return request_body, None
        compressed_body, algorithm = compress_payload(
            request_body,
            threshold=self._compression_threshold,
        )
        if algorithm == CompressionAlgorithm.IDENTITY:
            return request_body, None
        logger.debug(
            "asap.client.compression_applied",
            target_url=sanitize_url(self.base_url),
            envelope_id=envelope_id,
            algorithm=algorithm.value,
            original_size=len(json.dumps(json_rpc_request).encode("utf-8")),
            compressed_size=len(compressed_body),
        )
        return compressed_body, algorithm.value

    def _build_send_headers(
        self, idempotency_key: str, content_encoding: str | None
    ) -> dict[str, str]:
        """Build the per-attempt HTTP headers for a ``/asap`` POST."""
        accept_value = "application/json"
        if self._lambda_codec_enabled and lambda_codec.is_available():
            accept_value = f"{LAMBDA_CONTENT_TYPE}, application/json;q=0.9"
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": accept_value,
            "X-Idempotency-Key": idempotency_key,
            "Accept-Encoding": get_accept_encoding_header(),
            ASAP_VERSION_HEADER: self._asap_version_header_value,
        }
        if content_encoding:
            headers["Content-Encoding"] = content_encoding
        if self._auth_token:
            headers["Authorization"] = f"Bearer {self._auth_token}"
        return headers

    async def _handle_send_response(
        self,
        response: httpx.Response,
        envelope: Envelope,
        attempt: int,
        start_time: float,
        sanitized_url: str,
    ) -> Envelope:
        """Dispatch an HTTP response to the 5xx/429/4xx/2xx handlers.

        Returns the bound response envelope on success. Retryable 5xx/429/
        recoverable-RPC outcomes raise ``_RetryableSend`` on earlier attempts
        (signalling the caller to continue) and the final ASAP error on the
        last attempt; non-retriable 4xx raises ``ASAPConnectionError``.
        """
        status = response.status_code
        if status >= 500:
            return await self._handle_server_error(response, envelope, attempt, sanitized_url)
        if status == 429:
            return await self._handle_rate_limited(response, envelope, attempt, sanitized_url)
        if status >= 400:
            return self._handle_client_error(response, sanitized_url)
        return await self._handle_success_response(
            response, envelope, attempt, start_time, sanitized_url
        )

    async def _handle_server_error(
        self,
        response: httpx.Response,
        envelope: Envelope,
        attempt: int,
        sanitized_url: str,
    ) -> Envelope:
        """Retry or raise on a 5xx server error, recording circuit failure when exhausted."""
        error_msg = (
            f"HTTP server error {response.status_code} from {self.base_url}. "
            f"Server returned: {response.text[:200]}"
        )
        if attempt < self.max_retries - 1:
            delay = self._calculate_backoff(attempt)
            logger.warning(
                "asap.client.retry_server_error",
                status_code=response.status_code,
                attempt=attempt + 1,
                max_retries=self.max_retries,
                delay_seconds=round(delay, 2),
                target_url=sanitized_url,
                envelope_id=envelope.id,
                message=(
                    f"Server error {response.status_code}, retrying in {delay:.2f}s "
                    f"(attempt {attempt + 1}/{self.max_retries})"
                ),
            )
            await asyncio.sleep(delay)
            raise _RetryableSend(ASAPConnectionError(error_msg, url=self.base_url))
        if self._circuit_breaker is not None:
            self._circuit_breaker.record_failure()
            _log_circuit_event(self._circuit_breaker, base_url=sanitized_url, opened=True)
        raise ASAPConnectionError(error_msg, url=self.base_url)

    async def _handle_rate_limited(
        self,
        response: httpx.Response,
        envelope: Envelope,
        attempt: int,
        sanitized_url: str,
    ) -> Envelope:
        """Retry or raise on a 429, honouring ``Retry-After`` when parseable."""
        if attempt >= self.max_retries - 1:
            if self._circuit_breaker is not None:
                self._circuit_breaker.record_failure()
                _log_circuit_event(
                    self._circuit_breaker, base_url=sanitized_url, opened=True, rate_limited=True
                )
            raise ASAPConnectionError(
                f"HTTP rate limit error 429 from {self.base_url} after {self.max_retries} attempts. "
                f"Server response: {response.text[:200]}",
                url=sanitize_url(self.base_url),
            )

        retry_after = response.headers.get("Retry-After")
        retry_delay = _parse_retry_after(response)
        if retry_after and retry_delay is None:
            retry_delay = self._calculate_backoff(attempt)
            logger.warning(
                "asap.client.retry_after_invalid",
                target_url=sanitized_url,
                envelope_id=envelope.id,
                retry_after_header=retry_after,
                fallback_delay=round(retry_delay, 2),
                message="Invalid Retry-After format, using calculated backoff",
            )
        elif retry_delay is None:
            retry_delay = self._calculate_backoff(attempt)
        elif retry_after:
            logger.info(
                "asap.client.retry_after",
                target_url=sanitized_url,
                envelope_id=envelope.id,
                attempt=attempt + 1,
                retry_after_seconds=round(retry_delay, 2),
                message=f"Respecting server Retry-After: {round(retry_delay, 2)}s",
            )

        logger.warning(
            "asap.client.rate_limited",
            status_code=429,
            attempt=attempt + 1,
            max_retries=self.max_retries,
            delay_seconds=round(retry_delay, 2),
        )
        logger.info(
            "asap.client.retry",
            target_url=sanitized_url,
            envelope_id=envelope.id,
            attempt=attempt + 1,
            max_retries=self.max_retries,
            delay_seconds=round(retry_delay, 2),
        )
        await asyncio.sleep(retry_delay)
        raise _RetryableSend(
            ASAPConnectionError(
                f"HTTP rate limit error 429 from {self.base_url}. "
                f"Server response: {response.text[:200]}",
                url=sanitize_url(self.base_url),
            )
        )

    def _handle_client_error(self, response: httpx.Response, sanitized_url: str) -> Envelope:
        """Record a circuit failure and raise on a non-retriable 4xx error."""
        # Persistent 4xx (like 401/403) can indicate an unhealthy configuration.
        if self._circuit_breaker is not None:
            self._circuit_breaker.record_failure()
            _log_circuit_event(self._circuit_breaker, base_url=sanitized_url, opened=True)
        raise ASAPConnectionError(
            f"HTTP client error {response.status_code} from {self.base_url}. "
            f"This indicates a problem with the request. "
            f"Server response: {response.text[:200]}",
            url=sanitize_url(self.base_url),
        )

    async def _handle_success_response(
        self,
        response: httpx.Response,
        envelope: Envelope,
        attempt: int,
        start_time: float,
        sanitized_url: str,
    ) -> Envelope:
        """Parse a 2xx JSON-RPC response, bind it, record success, and log."""
        response_content_type = response.headers.get("content-type", "")
        try:
            if LAMBDA_CONTENT_TYPE in response_content_type:
                # Offload CPU-bound decoding to unblock the event loop
                json_str = await asyncio.to_thread(lambda_codec.decode, response.text)
                json_response = json.loads(json_str)
            else:
                json_response = response.json()
        except Exception as e:
            raise ASAPRemoteError.from_jsonrpc(-32700, f"Invalid response: {e}", None) from e

        if "error" in json_response:
            return await self._handle_jsonrpc_error(json_response, envelope, attempt, sanitized_url)

        result = json_response.get("result", {})
        envelope_data = result.get("envelope")
        if not envelope_data:
            raise ASAPRemoteError.from_jsonrpc(-32603, "Missing envelope in response", None)

        response_envelope = Envelope(**envelope_data)
        # BINDING check: a structurally valid response still must bind to the
        # request we sent, otherwise a buggy/malicious server could return a
        # response meant for a different request and mix pairs under concurrency.
        assert_correlation_binds(str(envelope.id), response_envelope)

        if self._circuit_breaker is not None:
            self._circuit_breaker.record_success()
            _log_circuit_event(self._circuit_breaker, base_url=sanitized_url, opened=False)

        duration_seconds = time.perf_counter() - start_time
        duration_ms = duration_seconds * 1000
        logger.info(
            "asap.client.response",
            target_url=sanitize_url(self.base_url),
            envelope_id=envelope.id,
            response_id=response_envelope.id,
            trace_id=envelope.trace_id,
            duration_ms=round(duration_ms, 2),
            attempts=attempt + 1,
        )
        metrics = get_metrics()
        metrics.increment_counter("asap_transport_send_total", {"status": "success"})
        metrics.observe_histogram(
            "asap_transport_send_duration_seconds",
            duration_seconds,
            {"status": "success"},
        )
        return response_envelope

    async def _handle_jsonrpc_error(
        self,
        json_response: dict[str, Any],
        envelope: Envelope,
        attempt: int,
        sanitized_url: str,
    ) -> Envelope:
        """Process a JSON-RPC ``error`` body: recoverable retry or raise."""
        if self._circuit_breaker is not None:
            self._circuit_breaker.record_success()

        error = json_response["error"]
        err_payload = error.get("data")
        err_data = err_payload if isinstance(err_payload, dict) else None
        wire_code = int(error.get("code", -32603))
        err_msg = str(error.get("message", "Unknown error"))
        rpc_exc = remote_rpc_error_from_json(wire_code, err_msg, err_data)
        if (
            rpc_exc.is_recoverable
            and rpc_exc.retry_after_ms is not None
            and attempt < self.max_retries - 1
        ):
            delay_s = max(0.0, rpc_exc.retry_after_ms / 1000.0)
            logger.info(
                "asap.client.retry_recoverable_rpc_error",
                target_url=sanitized_url,
                envelope_id=envelope.id,
                attempt=attempt + 1,
                retry_after_ms=rpc_exc.retry_after_ms,
                message="Retrying after server recoverable JSON-RPC error hint",
            )
            await asyncio.sleep(delay_s)
            raise _RetryableSend(rpc_exc)
        raise rpc_exc

    async def _handle_send_transport_exception(
        self,
        error: httpx.ConnectError | httpx.TimeoutException,
        envelope: Envelope,
        attempt: int,
        start_time: float,
        sanitized_url: str,
    ) -> Exception:
        """Handle a connect/timeout exception and drive the retry decision.

        On a retryable attempt: sleeps the backoff delay and returns the stored
        exception so the caller's loop can ``continue``. On the final attempt:
        records the circuit failure, logs the final failure, and raises the
        stored exception.
        """
        is_timeout = isinstance(error, httpx.TimeoutException)
        error_type = "Timeout" if is_timeout else "Connection error"
        error_msg = (
            f"{error_type} to {self.base_url}: {error}. Verify the agent is running and accessible."
        )
        last_exception: Exception
        if is_timeout:
            last_exception = ASAPTimeoutError(
                f"Request timeout after {self.timeout}s", timeout=self.timeout
            )
        else:
            last_exception = ASAPConnectionError(error_msg, cause=error, url=self.base_url)

        if attempt < self.max_retries - 1:
            delay = self._calculate_backoff(attempt)
            logger.warning(
                "asap.client.retry",
                target_url=sanitized_url,
                envelope_id=envelope.id,
                attempt=attempt + 1,
                max_retries=self.max_retries,
                error=str(error),
                delay_seconds=round(delay, 2),
                message=(
                    f"{error_type} to {self.base_url} (attempt {attempt + 1}/{self.max_retries}). "
                    f"Retrying in {delay:.2f}s. Error: {str(error)[:100]}"
                ),
            )
            await asyncio.sleep(delay)
            return last_exception

        # All retries exhausted: record failure, log final, and re-raise.
        if self._circuit_breaker is not None:
            self._circuit_breaker.record_failure()
            _log_circuit_event(self._circuit_breaker, base_url=sanitized_url, opened=True)
        duration_ms = (time.perf_counter() - start_time) * 1000
        error_type_name = "ASAPTimeoutError" if is_timeout else "ASAPConnectionError"
        logger.error(
            "asap.client.error",
            target_url=sanitized_url,
            envelope_id=envelope.id,
            error=f"{error_type} after retries",
            error_type=error_type_name,
            duration_ms=round(duration_ms, 2),
            attempts=attempt + 1,
            max_retries=self.max_retries,
            timeout=self.timeout if is_timeout else None,
            message=(
                f"{error_type} to {self.base_url} failed after {attempt + 1} attempts. "
                f"Total duration: {duration_ms:.2f}ms. "
                f"Troubleshooting: Verify the agent is running, check network connectivity, "
                f"and ensure the URL is correct. Original error: {str(error)[:200]}"
            ),
        )
        raise last_exception from error

    async def stream(self, envelope: Envelope) -> AsyncIterator[Envelope]:
        """POST to ``/asap/stream`` and yield each SSE ``data:`` line as an ``Envelope``.

        Requires HTTP transport (not WebSocket). Each event body is a full envelope,
        typically with ``payload_type`` ``TaskStream``. Streamed response payloads
        and ``TaskStream`` chunks are correlation-bound to ``envelope.id`` before
        they are yielded (CR#4).

        Args:
            envelope: Outgoing request envelope (e.g. ``TaskRequest``).

        Yields:
            One envelope per SSE event.

        Raises:
            ASAPConnectionError: On HTTP errors or non-streaming failure responses.
            ASAPRemoteError: If the server returns a JSON-RPC error body instead of SSE.
        """
        if envelope is None:
            raise ValueError("envelope cannot be None")
        if not self._client:
            raise ASAPConnectionError(
                "Client not connected. Use 'async with' context.",
                url=sanitize_url(self.base_url),
            )
        if self._ws_transport:
            raise ASAPConnectionError(
                "HTTP streaming only: disconnect WebSocket mode or use "
                "WebSocketTransport.send_and_receive_stream for /asap/ws.",
                url=sanitize_url(self.base_url),
            )

        idempotency_key = generate_id()
        request_id = f"req-{next(self._request_counter)}"
        json_rpc_request = {
            "jsonrpc": "2.0",
            "method": ASAP_METHOD,
            "params": {
                "envelope": envelope.model_dump(mode="json"),
                "idempotency_key": idempotency_key,
            },
            "id": request_id,
        }
        request_body = json.dumps(json_rpc_request).encode("utf-8")
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "X-Idempotency-Key": idempotency_key,
            ASAP_VERSION_HEADER: self._asap_version_header_value,
        }
        if self._auth_token:
            headers["Authorization"] = f"Bearer {self._auth_token}"

        if self._client is None:
            raise RuntimeError(
                "ASAPClient must be used as an async context manager before streaming"
            )
        async with self._client.stream(
            "POST",
            f"{self.base_url}/asap/stream",
            headers=headers,
            content=request_body,
        ) as response:
            self._capture_asap_version_header(response)
            if response.status_code >= 400:
                body = await response.aread()
                try:
                    text = body.decode("utf-8")
                    err_json = json.loads(text)
                    if isinstance(err_json, dict) and "error" in err_json:
                        error = err_json["error"]
                        wire_code = int(error.get("code", -32603))
                        err_msg = str(error.get("message", "Unknown error"))
                        err_payload = error.get("data")
                        err_data = err_payload if isinstance(err_payload, dict) else None
                        raise remote_rpc_error_from_json(wire_code, err_msg, err_data)
                except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                    pass
                raise ASAPConnectionError(
                    f"HTTP error {response.status_code} on stream: {body[:200]!r}",
                    url=sanitize_url(self.base_url),
                )

            buffer = ""
            async for chunk in response.aiter_text():
                buffer += chunk
                while "\n\n" in buffer:
                    raw_event, buffer = buffer.split("\n\n", 1)
                    for line in raw_event.split("\n"):
                        line_stripped = line.strip()
                        if not line_stripped.startswith("data:"):
                            continue
                        json_str = line_stripped[5:].strip()
                        if not json_str:
                            continue
                        data = json.loads(json_str)
                        stream_envelope = Envelope.model_validate(data)
                        # BINDING: streamed chunks still answer the specific
                        # request that opened this SSE stream, so reject chunks
                        # whose correlation_id points at a different request.
                        assert_stream_correlation_binds(str(envelope.id), stream_envelope)
                        yield stream_envelope
