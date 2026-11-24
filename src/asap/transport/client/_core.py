"""Facade and lifecycle for :class:`asap.transport.client.ASAPClient`.

Defines the public ``ASAPClient`` class, including construction, async context
management, connection validation, retry configuration, version negotiation,
challenge handling, and batch operations. Send and discovery behavior are mixed
in from focused client modules.
"""

from __future__ import annotations

import asyncio
import itertools
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Literal, Mapping, Optional, Sequence
from urllib.parse import ParseResult, urlparse, urlunparse

import httpx

from asap.errors import ASAPConnectionError, ASAPRemoteError
from asap.models.constants import (
    ASAP_SUPPORTED_TRANSPORT_VERSIONS,
    ASAP_VERSION_HEADER,
    DEFAULT_BASE_DELAY,
    DEFAULT_CIRCUIT_BREAKER_THRESHOLD,
    DEFAULT_CIRCUIT_BREAKER_TIMEOUT,
    DEFAULT_MAX_DELAY,
)
from asap.models.envelope import Envelope
from asap.transport.cache import DEFAULT_MAX_SIZE, ManifestCache
from asap.transport.challenge import parse_www_authenticate_asap
from asap.transport.circuit_breaker import CircuitBreaker, get_registry
from asap.transport.client._discovery import _DiscoveryMixin
from asap.transport.client._helpers import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_POOL_CONNECTIONS,
    DEFAULT_POOL_MAXSIZE,
    DEFAULT_POOL_TIMEOUT,
    DEFAULT_TIMEOUT,
    logger,
)
from asap.transport.client._send import _SendMixin
from asap.transport.lambda_codec import LAMBDA_CONTENT_TYPE  # noqa: F401 (re-exported surface)
from asap.transport.compression import COMPRESSION_THRESHOLD
from asap.transport.errors import ProtocolCorrelationError, assert_correlation_binds
from asap.transport.jsonrpc import ASAP_METHOD
from asap.transport.mtls import MTLSConfig, create_ssl_context
from asap.transport.ws import (
    DEFAULT_ACK_TIMEOUT,
    DEFAULT_MAX_ACK_RETRIES,
    OnMessageCallback,
    WebSocketTransport,
)
from asap.utils.sanitization import sanitize_url

from asap.errors import remote_rpc_error_from_json


@dataclass
class RetryConfig:
    """Configuration for retry logic and circuit breaker.

    Groups retry and circuit breaker parameters to simplify client initialization
    and avoid boolean trap issues.

    Attributes:
        max_retries: Maximum retry attempts for transient failures (default: 3)
        base_delay: Base delay in seconds for exponential backoff (default: 1.0)
        max_delay: Maximum delay in seconds for exponential backoff (default: 60.0)
        jitter: Whether to add random jitter to backoff delays (default: True)
        circuit_breaker_enabled: Enable circuit breaker pattern (default: False)
        circuit_breaker_threshold: Number of consecutive failures before opening circuit (default: 5)
        circuit_breaker_timeout: Seconds before transitioning OPEN -> HALF_OPEN (default: 60.0)
    """

    max_retries: int = DEFAULT_MAX_RETRIES
    base_delay: float = DEFAULT_BASE_DELAY
    max_delay: float = DEFAULT_MAX_DELAY
    jitter: bool = True
    circuit_breaker_enabled: bool = False
    circuit_breaker_threshold: int = DEFAULT_CIRCUIT_BREAKER_THRESHOLD
    circuit_breaker_timeout: float = DEFAULT_CIRCUIT_BREAKER_TIMEOUT


class ASAPClient(_SendMixin, _DiscoveryMixin):
    """Async HTTP client for ASAP protocol communication.

    ASAPClient manages HTTP connections to remote ASAP agents and provides
    methods for sending envelopes and receiving responses.

    **Use as async context manager:** Always use ``async with ASAPClient(...) as client``
    to ensure the underlying HTTP client is started and closed properly. Using the
    client without the context manager may leave connections open.

    Features:
        - HTTP/2 multiplexing (enabled by default) for improved batch performance
        - Connection pooling supporting 1000+ concurrent requests
        - Automatic retry with exponential backoff
        - Circuit breaker pattern for fault tolerance
        - Batch operations via send_batch() method (HTTP only; WebSocket transport
          raises NotImplementedError)
        - Compression support (gzip/brotli) for bandwidth reduction

    Attributes:
        base_url: Base URL of the remote agent
        timeout: Request timeout in seconds
        max_retries: Maximum retry attempts for transient failures
        require_https: Whether HTTPS is required for non-localhost connections
        is_connected: Whether the client has an active connection
        compression: Whether compression is enabled for requests
        compression_threshold: Minimum payload size to trigger compression
        _circuit_breaker: Optional circuit breaker instance
        last_response_asap_version: ``ASAP-Version`` header from the last HTTP
            ``/asap`` or ``/asap/stream`` response (``None`` if missing)

    Pool sizing (pool_connections / pool_maxsize):
        Single-agent: 100 (default). Small cluster: 200–500. Large cluster: 500–1000.
        Supports 1000+ concurrent requests via connection reuse when pool_maxsize < concurrency.

    HTTP/2 Multiplexing:
        HTTP/2 is enabled by default (http2=True) and provides request multiplexing over
        a single TCP connection, reducing latency for batch operations. If the server
        doesn't support HTTP/2, the client automatically falls back to HTTP/1.1.

    Compression:
        Compression is enabled by default (compression=True) for payloads exceeding
        1KB. Supports gzip (standard) and brotli (optional, requires brotli package).
        Brotli provides ~20% better compression than gzip for JSON payloads.

    Example:
        >>> async with ASAPClient("http://localhost:8000") as client:
        ...     response = await client.send(envelope)
        >>>
        >>> # Batch operations with HTTP/2 multiplexing
        >>> async with ASAPClient("https://agent.example.com") as client:
        ...     responses = await client.send_batch([env1, env2, env3])
        >>>
        >>> # Disable compression for specific client
        >>> async with ASAPClient("http://localhost:8000", compression=False) as client:
        ...     response = await client.send(envelope)  # No compression
    """

    _circuit_breaker: Optional[CircuitBreaker]

    def __init__(
        self,
        base_url: str,
        timeout: float = DEFAULT_TIMEOUT,
        transport_mode: Literal["http", "websocket", "auto"] = "auto",
        transport: httpx.AsyncBaseTransport | None = None,
        require_https: bool = True,
        retry_config: Optional[RetryConfig] = None,
        # Connection pool (httpx.Limits); enables 1000+ concurrent via reuse
        pool_connections: int | None = None,
        pool_maxsize: int | None = None,
        pool_timeout: float | None = None,
        # HTTP/2 multiplexing for improved batch performance
        http2: bool = True,
        # Compression settings for bandwidth reduction
        compression: bool = True,
        compression_threshold: int = COMPRESSION_THRESHOLD,
        # Individual retry parameters (for backward compatibility)
        # If retry_config is provided, these are ignored
        max_retries: int | None = None,
        base_delay: float | None = None,
        max_delay: float | None = None,
        jitter: bool | None = None,
        circuit_breaker_enabled: bool | None = None,
        circuit_breaker_threshold: int | None = None,
        circuit_breaker_timeout: float | None = None,
        lambda_codec_enabled: bool = False,
        manifest_cache_size: int | None = None,
        verify_signatures: bool = False,
        trusted_manifest_keys: Optional[Mapping[str, str]] = None,
        on_message: Optional[OnMessageCallback] = None,
        mtls_config: Optional[MTLSConfig] = None,
        supported_transport_versions: Sequence[str] | None = None,
        auth_token: str | None = None,
        auto_register_on_asap_challenge: bool = False,
    ) -> None:
        # Extract retry config values
        if retry_config is not None:
            # Use retry_config values
            max_retries_val = retry_config.max_retries
            base_delay_val = retry_config.base_delay
            max_delay_val = retry_config.max_delay
            jitter_val = retry_config.jitter
            circuit_breaker_enabled_val = retry_config.circuit_breaker_enabled
            circuit_breaker_threshold_val = retry_config.circuit_breaker_threshold
            circuit_breaker_timeout_val = retry_config.circuit_breaker_timeout
        else:
            # Use individual parameters with defaults
            max_retries_val = max_retries if max_retries is not None else DEFAULT_MAX_RETRIES
            base_delay_val = base_delay if base_delay is not None else DEFAULT_BASE_DELAY
            max_delay_val = max_delay if max_delay is not None else DEFAULT_MAX_DELAY
            jitter_val = jitter if jitter is not None else True
            circuit_breaker_enabled_val = (
                circuit_breaker_enabled if circuit_breaker_enabled is not None else False
            )
            circuit_breaker_threshold_val = (
                circuit_breaker_threshold
                if circuit_breaker_threshold is not None
                else DEFAULT_CIRCUIT_BREAKER_THRESHOLD
            )
            circuit_breaker_timeout_val = (
                circuit_breaker_timeout
                if circuit_breaker_timeout is not None
                else DEFAULT_CIRCUIT_BREAKER_TIMEOUT
            )
        parsed = urlparse(base_url)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError(
                f"Invalid base_url format: {base_url}. Must be a valid URL (e.g., http://localhost:8000)"
            )

        scheme_lower = parsed.scheme.lower()
        allowed_schemes = (
            ("http", "https", "ws", "wss")
            if transport_mode in ("websocket", "auto")
            else ("http", "https")
        )
        if scheme_lower not in allowed_schemes:
            raise ValueError(
                f"Invalid URL scheme: {parsed.scheme}. Allowed: {', '.join(allowed_schemes)}. "
                f"Received: {base_url}"
            )

        use_websocket = transport_mode == "websocket" or (
            transport_mode == "auto" and scheme_lower in ("ws", "wss")
        )
        if use_websocket:
            ws_url = base_url.rstrip("/")
            if scheme_lower in ("http", "https"):
                ws_scheme = "wss" if scheme_lower == "https" else "ws"
                ws_netloc = parsed.netloc
                ws_path = (parsed.path or "/").rstrip("/") + "/asap/ws"
                ws_url = urlunparse((ws_scheme, ws_netloc, ws_path, "", "", ""))
            elif not ws_url.endswith("/asap/ws"):
                ws_url = ws_url.rstrip("/") + "/asap/ws"
        else:
            ws_url = ""

        is_https = scheme_lower in ("https", "wss")
        is_local = self._is_localhost(parsed)

        if require_https and not is_https:
            if is_local:
                # Allow unencrypted transport for localhost with warning
                logger.warning(
                    "asap.client.http_localhost",
                    url=base_url,
                    message=(
                        "Using unencrypted transport for localhost connection. "
                        "For production, use HTTPS. "
                        "To disable this warning, set require_https=False."
                    ),
                )
            else:
                # Reject unencrypted transport for non-localhost
                raise ValueError(
                    f"Encrypted transport (https/wss) is required for non-localhost connections. "
                    f"Received: {base_url}. "
                    f"Please use HTTPS/WSS or set require_https=False to override "
                    f"(not recommended for production)."
                )

        self.base_url = base_url.rstrip("/")
        self._use_websocket = use_websocket
        self._ws_url = ws_url
        if use_websocket:
            if scheme_lower in ("ws", "wss"):
                http_scheme = "https" if scheme_lower == "wss" else "http"
                path = (parsed.path or "/").replace("/asap/ws", "").rstrip("/") or "/"
                self._http_base_url = urlunparse((http_scheme, parsed.netloc, path, "", "", ""))
            else:
                self._http_base_url = self.base_url
        else:
            self._http_base_url = self.base_url
        self.timeout = timeout
        self._pool_connections = (
            pool_connections if pool_connections is not None else DEFAULT_POOL_CONNECTIONS
        )
        self._pool_maxsize = pool_maxsize if pool_maxsize is not None else DEFAULT_POOL_MAXSIZE
        self._pool_timeout = pool_timeout if pool_timeout is not None else DEFAULT_POOL_TIMEOUT
        self.max_retries = max_retries_val
        self.require_https = require_https
        self.base_delay = base_delay_val
        self.max_delay = max_delay_val
        self.jitter = jitter_val
        self.circuit_breaker_enabled = circuit_breaker_enabled_val
        self._transport = transport
        self._http2 = http2
        self._compression = compression
        self._compression_threshold = compression_threshold
        self._lambda_codec_enabled = lambda_codec_enabled
        self._on_message = on_message
        self._client: httpx.AsyncClient | None = None
        self._ws_transport: WebSocketTransport | None = None
        # Thread-safe counter using itertools.count
        self._request_counter = itertools.count(1)

        # Circuit breaker state is shared across clients for the same base URL.
        if circuit_breaker_enabled_val:
            registry = get_registry()
            self._circuit_breaker = registry.get_or_create(
                base_url=sanitize_url(self.base_url),
                threshold=circuit_breaker_threshold_val,
                timeout=circuit_breaker_timeout_val,
            )
        else:
            self._circuit_breaker = None

        # Per-client manifest cache (not shared like circuit breaker).
        cache_max = manifest_cache_size if manifest_cache_size is not None else DEFAULT_MAX_SIZE
        self._manifest_cache = ManifestCache(max_size=cache_max)
        self._manifest_fetch_locks: dict[str, asyncio.Lock] = {}
        self._manifest_fetch_locks_guard = threading.Lock()
        self._verify_signatures = verify_signatures
        self._trusted_manifest_keys = dict(trusted_manifest_keys) if trusted_manifest_keys else {}
        self._mtls_config = mtls_config
        self._auth_token = auth_token

        if supported_transport_versions is None:
            self._transport_versions = tuple(
                sorted(ASAP_SUPPORTED_TRANSPORT_VERSIONS, reverse=True)
            )
        else:
            tv = tuple(supported_transport_versions)
            if not tv:
                raise ValueError("supported_transport_versions must be non-empty when provided")
            unknown = set(tv) - set(ASAP_SUPPORTED_TRANSPORT_VERSIONS)
            if unknown:
                raise ValueError(
                    f"Unsupported ASAP transport versions {sorted(unknown)}. "
                    f"Allowed: {sorted(ASAP_SUPPORTED_TRANSPORT_VERSIONS)}"
                )
            self._transport_versions = tv
        self._asap_version_header_value = ", ".join(self._transport_versions)
        self._last_response_asap_version: str | None = None
        self._auto_register_on_asap_challenge = auto_register_on_asap_challenge
        self._last_asap_challenge_discovery_url: str | None = None

    @staticmethod
    def _is_localhost(parsed_url: ParseResult) -> bool:
        hostname = parsed_url.hostname
        if not hostname:
            return False

        hostname_lower = hostname.lower()
        # Handle both ::1 and [::1] (bracket notation from URL parsing)
        return hostname_lower in ("localhost", "127.0.0.1", "::1", "[::1]")

    def _calculate_backoff(self, attempt: int) -> float:
        # Calculate exponential delay: base_delay * (2 ** attempt)
        delay = self.base_delay * (2**attempt)

        # Cap at max_delay
        delay = min(delay, self.max_delay)

        if self.jitter:
            jitter_amount: float = secrets.SystemRandom().uniform(0, delay * 0.1)
            delay += jitter_amount

        return float(delay)

    def _capture_asap_version_header(self, response: httpx.Response) -> None:
        """Store ``ASAP-Version`` from an HTTP response for inspection."""
        raw = response.headers.get(ASAP_VERSION_HEADER.lower())
        self._last_response_asap_version = raw.strip() if raw else None

    async def _ingest_asap_challenge_401(self, response: httpx.Response) -> None:
        """Record ASAP discovery from ``WWW-Authenticate`` and optionally prefetch manifest."""
        raw = response.headers.get("www-authenticate")
        disc = parse_www_authenticate_asap(raw)
        self._last_asap_challenge_discovery_url = disc
        if not disc or not self._auto_register_on_asap_challenge:
            return
        try:
            await self.get_manifest(disc)
        except Exception:
            logger.debug(
                "asap.client.asap_challenge_prefetch_failed",
                discovery_url=disc,
                exc_info=True,
            )

    @property
    def last_asap_challenge_discovery_url(self) -> str | None:
        """Discovery URL from the last HTTP 401 ``WWW-Authenticate: ASAP`` response."""
        return self._last_asap_challenge_discovery_url

    @property
    def last_response_asap_version(self) -> str | None:
        """Negotiated wire version from the last ``/asap`` or ``/asap/stream`` response."""
        return self._last_response_asap_version

    async def _validate_connection(self) -> bool:
        """Validate that the agent endpoint is accessible.

        Performs a pre-flight check by attempting to access the agent's
        manifest endpoint. This can be used to detect connection issues
        before sending actual requests.

        Note: This is an optional validation step that can be disabled
        for performance reasons in production environments.

        Returns:
            True if connection is valid, False otherwise

        Raises:
            ASAPConnectionError: If connection validation fails
        """
        if not self._client:
            raise ASAPConnectionError(
                "Client not connected. Use 'async with' context.",
                url=sanitize_url(self.base_url),
            )

        try:
            # Try to access a lightweight endpoint (manifest or health check)
            # Using HEAD request to minimize bandwidth
            response = await self._client.head(
                f"{self._http_base_url}/.well-known/asap/manifest.json",
                timeout=min(self.timeout, 5.0),  # Shorter timeout for validation
            )
            # Any 2xx or 3xx response indicates the server is reachable
            is_valid = 200 <= response.status_code < 400
            if not is_valid:
                logger.warning(
                    "asap.client.connection_validation_failed",
                    target_url=sanitize_url(self.base_url),
                    status_code=response.status_code,
                    message=(
                        f"Connection validation failed for {self.base_url}. "
                        f"Server returned status {response.status_code}. "
                        f"Verify the agent is running and the URL is correct."
                    ),
                )
            return is_valid
        except httpx.ConnectError as e:
            logger.warning(
                "asap.client.connection_validation_failed",
                target_url=sanitize_url(self.base_url),
                error=str(e),
                message=(
                    f"Connection validation failed for {self.base_url}. "
                    f"Cannot reach the agent. Verify the agent is running and accessible. "
                    f"Error: {str(e)[:200]}"
                ),
            )
            return False
        except httpx.TimeoutException:
            logger.warning(
                "asap.client.connection_validation_timeout",
                target_url=sanitize_url(self.base_url),
                timeout=self.timeout,
                message=(
                    f"Connection validation timed out for {self.base_url}. "
                    f"Check network connectivity and firewall settings."
                ),
            )
            return False
        except Exception as e:
            logger.warning(
                "asap.client.connection_validation_error",
                target_url=sanitize_url(self.base_url),
                error=str(e),
                error_type=type(e).__name__,
                message=(
                    f"Connection validation encountered an error for {self.base_url}: {e}. "
                    f"Verify the agent is running and accessible."
                ),
            )
            return False

    @property
    def is_connected(self) -> bool:
        """Check if client has an active connection."""
        return self._client is not None or self._ws_transport is not None

    def _httpx_mtls_kwargs(
        self,
    ) -> tuple[tuple[str, str] | tuple[str, str, str] | None, bool | str]:
        if self._mtls_config is None:
            return (None, True)
        cfg = self._mtls_config
        cert: tuple[str, str] | tuple[str, str, str] = (
            str(cfg.cert_file),
            str(cfg.key_file),
        )
        if cfg.key_password:
            cert = (str(cfg.cert_file), str(cfg.key_file), cfg.key_password)
        verify: bool | str = str(cfg.ca_certs) if cfg.ca_certs else True
        return (cert, verify)

    async def __aenter__(self) -> "ASAPClient":
        cert, verify = self._httpx_mtls_kwargs()
        ws_ssl_context = None
        if self._mtls_config is not None:
            ws_ssl_context = create_ssl_context(self._mtls_config, purpose="client")
        if self._use_websocket:
            # Carry the HTTP ``auth_token`` onto the WS handshake so OAuth2-only
            # deployments don't reject the connection with 4401 (CR#3).
            ws_extra_headers: dict[str, str] | None = None
            if self._auth_token:
                ws_extra_headers = {"Authorization": f"Bearer {self._auth_token}"}
            self._ws_transport = WebSocketTransport(
                receive_timeout=self.timeout,
                on_message=self._on_message,
                ack_timeout_seconds=DEFAULT_ACK_TIMEOUT,
                max_ack_retries=DEFAULT_MAX_ACK_RETRIES,
                circuit_breaker=self._circuit_breaker,
                ssl_context=ws_ssl_context,
                extra_headers=ws_extra_headers,
            )
            await self._ws_transport.connect(self._ws_url)
            # WebSocket mode still uses HTTP client for manifest fetches; small pool is enough.
            limits = httpx.Limits(max_connections=2, max_keepalive_connections=1)
            timeout_config = httpx.Timeout(self.timeout, pool=self._pool_timeout)
            self._client = httpx.AsyncClient(
                timeout=timeout_config,
                limits=limits,
                cert=cert,
                verify=verify,
            )
        else:
            limits = httpx.Limits(
                max_keepalive_connections=self._pool_connections,
                max_connections=self._pool_maxsize,
                keepalive_expiry=DEFAULT_POOL_TIMEOUT,
            )
            timeout_config = httpx.Timeout(self.timeout, pool=self._pool_timeout)
            if self._transport:
                self._client = httpx.AsyncClient(
                    transport=self._transport,
                    timeout=timeout_config,
                    limits=limits,
                    cert=cert,
                    verify=verify,
                )
            else:
                self._client = httpx.AsyncClient(
                    timeout=timeout_config,
                    limits=limits,
                    http2=self._http2,
                    cert=cert,
                    verify=verify,
                )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        if self._ws_transport:
            await self._ws_transport.close()
            self._ws_transport = None
        if self._client:
            await self._client.aclose()
            self._client = None

    async def send_batch(
        self,
        envelopes: list[Envelope],
        return_exceptions: bool = False,
    ) -> list[Envelope | BaseException]:
        """Send multiple envelopes in parallel using asyncio.gather.

        Uses asyncio.gather to send all envelopes concurrently, leveraging
        connection pooling and HTTP/2 multiplexing for optimal throughput.

        Args:
            envelopes: List of ASAP envelopes to send
            return_exceptions: If True, exceptions are returned in the result list
                instead of being raised. If False (default), the first exception
                encountered will be raised.

        Returns:
            List of response envelopes in the same order as input envelopes.
            If return_exceptions=True, failed sends will have the exception
            in their position instead of an Envelope.

        Raises:
            ValueError: If envelopes list is empty
            ASAPConnectionError: If any send fails (when return_exceptions=False)
            ASAPTimeoutError: If any send times out (when return_exceptions=False)
            ASAPRemoteError: If any remote agent returns error (when return_exceptions=False)
            CircuitOpenError: If circuit breaker is open (when return_exceptions=False)

        Example:
            >>> async with ASAPClient("http://localhost:8000") as client:
            ...     responses = await client.send_batch([env1, env2, env3])
            ...     for response in responses:
            ...         print(response.payload_type)
            >>>
            >>> # With error handling
            >>> async with ASAPClient("http://localhost:8000") as client:
            ...     results = await client.send_batch(envelopes, return_exceptions=True)
            ...     for i, result in enumerate(results):
            ...         if isinstance(result, BaseException):
            ...             print(f"Envelope {i} failed: {result}")
            ...         else:
            ...             print(f"Envelope {i} succeeded: {result.id}")
        """
        if not envelopes:
            raise ValueError("envelopes list cannot be empty")

        if self._ws_transport:
            raise NotImplementedError(
                "send_batch is not supported with WebSocket transport; use send() in a loop."
            )

        if not self._client:
            raise ASAPConnectionError(
                "Client not connected. Use 'async with' context.",
                url=sanitize_url(self.base_url),
            )

        batch_size = len(envelopes)
        logger.info(
            "asap.client.send_batch",
            target_url=sanitize_url(self.base_url),
            batch_size=batch_size,
            message=f"Sending batch of {batch_size} envelopes to {sanitize_url(self.base_url)}",
        )

        start_time = time.perf_counter()

        # Create send tasks for all envelopes
        tasks = [self.send(envelope) for envelope in envelopes]

        # Execute all tasks concurrently
        results = await asyncio.gather(*tasks, return_exceptions=return_exceptions)

        duration_ms = (time.perf_counter() - start_time) * 1000

        # Count successes and failures
        if return_exceptions:
            success_count = sum(1 for r in results if isinstance(r, Envelope))
            failure_count = batch_size - success_count
        else:
            success_count = batch_size
            failure_count = 0

        logger.info(
            "asap.client.send_batch_complete",
            target_url=sanitize_url(self.base_url),
            batch_size=batch_size,
            success_count=success_count,
            failure_count=failure_count,
            duration_ms=round(duration_ms, 2),
            throughput_per_second=round(batch_size / (duration_ms / 1000), 2)
            if duration_ms > 0
            else 0,
            message=(
                f"Batch of {batch_size} envelopes completed in {duration_ms:.2f}ms "
                f"({success_count} succeeded, {failure_count} failed)"
            ),
        )

        return results

    async def batch(self, envelopes: list[Envelope]) -> list[Envelope]:
        """Send a JSON-RPC batch request (single HTTP call with array body).

        Unlike ``send_batch`` (which sends individual requests in parallel),
        this method serializes all requests into a single JSON array body
        per the JSON-RPC 2.0 batch specification.

        Args:
            envelopes: List of envelopes to send as a batch.

        Returns:
            List of response envelopes (one per input).

        Raises:
            ValueError: If envelopes list is empty.
            ASAPConnectionError: If client is not connected.
            NotImplementedError: If WebSocket transport is active.
            ASAPRemoteError: If any sub-request returns an error.
        """
        if not envelopes:
            raise ValueError("envelopes list cannot be empty")
        if self._ws_transport:
            raise NotImplementedError("batch is not supported with WebSocket transport.")
        if not self._client:
            raise ASAPConnectionError(
                "Client not connected. Use 'async with' context.",
                url=sanitize_url(self.base_url),
            )

        batch_body: list[dict[str, Any]] = []
        # Map JSON-RPC id -> originating request envelope so each batch sub-response
        # can be bound to its request by correlation_id (B6/BUG #6). A malicious/buggy
        # peer could otherwise permute response bodies while keeping HTTP 200 and the
        # client would pair the wrong task.response to each batch slot.
        requests_by_id: dict[str, Envelope] = {}
        for env in envelopes:
            rpc_req = {
                "jsonrpc": "2.0",
                "method": ASAP_METHOD,
                "params": {"envelope": env.model_dump(mode="json")},
                "id": env.id,
            }
            batch_body.append(rpc_req)
            requests_by_id[str(env.id)] = env

        url = f"{self.base_url}/asap"
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            ASAP_VERSION_HEADER: self._asap_version_header_value,
        }
        if self._auth_token:
            headers["Authorization"] = f"Bearer {self._auth_token}"
        response = await self._client.post(url, json=batch_body, headers=headers)
        response.raise_for_status()

        results = response.json()
        if not isinstance(results, list):
            raise ASAPRemoteError(
                wire_jsonrpc_code=-32603,
                message=f"Batch response is not an array from {sanitize_url(url)}",
            )

        out: list[Envelope] = []
        for item in results:
            if "error" in item:
                err = item["error"]
                wire_code = int(err.get("code", -32603))
                err_msg = str(err.get("message", "Batch sub-request error"))
                err_data = err.get("data") if isinstance(err.get("data"), dict) else None
                raise remote_rpc_error_from_json(wire_code, err_msg, err_data)
            result_data = item.get("result", {})
            env_data = result_data.get("envelope", result_data)
            response_envelope = Envelope.model_validate(env_data)
            # BINDING check (B6/BUG #6): each batch sub-response must bind to its
            # originating request by correlation_id. Resolve the request via the
            # JSON-RPC id; if the peer permuted ids or returned a response for a
            # request we did not send, reject it.
            rpc_id = item.get("id")
            request_envelope = requests_by_id.get(str(rpc_id)) if rpc_id is not None else None
            if request_envelope is None:
                raise ProtocolCorrelationError(
                    request_id=str(rpc_id) if rpc_id is not None else "",
                    correlation_id=response_envelope.correlation_id,
                    details={"reason": "batch sub-response id does not match any sent request"},
                )
            assert_correlation_binds(str(request_envelope.id), response_envelope)
            out.append(response_envelope)
        return out
