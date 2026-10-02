"""Webhook delivery for ASAP event callbacks.

This module provides secure delivery of webhook payloads to registered callback URLs
with SSRF protection, HMAC signing, configurable timeouts, and retry logic.

Public exports:
    WebhookDelivery: Delivers signed POST callbacks to validated URLs.
    WebhookRetryManager: Wraps WebhookDelivery with retry queue, backoff, DLQ, and rate limiting.
    RetryPolicy: Configuration for retry behaviour (max retries, backoff, retryable codes).
    DeadLetterEntry: Record of a permanently failed webhook delivery.
    validate_callback_url: Validates a URL against SSRF rules (scheme, DNS, IP range).
    validate_agent_endpoint_url: Same checks for HTTP/HTTPS/WSS registry endpoint fields.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import socket
import time
from collections import deque
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx

from asap.errors import WebhookURLValidationError
from asap.observability import get_logger

logger = get_logger(__name__)

# Header used for HMAC signature.
X_ASAP_SIGNATURE_HEADER = "X-ASAP-Signature"

# Default timeout for webhook HTTP requests (seconds).
DEFAULT_WEBHOOK_TIMEOUT = 10.0

# Maximum number of dead-letter entries to retain (evict oldest when exceeded).
MAX_DEAD_LETTERS = 1000

# Allowed URL schemes.
_ALLOWED_SCHEMES_STRICT = frozenset({"https"})
_ALLOWED_SCHEMES_RELAXED = frozenset({"http", "https"})
_WS_TO_HTTP_SCHEMES = {"ws": "http", "wss": "https"}


def _is_ip_blocked(addr: str) -> bool:
    """True if addr is private, loopback, link-local, or reserved."""
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        # Not a valid IP literal — caller must resolve hostname first.
        return False

    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved


async def _resolve_hostname(hostname: str) -> list[str]:
    """Resolve hostname to deduplicated IP list via async getaddrinfo.

    Uses ``loop.getaddrinfo`` to avoid blocking the event loop during DNS
    resolution. Raises ``WebhookURLValidationError`` on failure.
    """
    loop = asyncio.get_running_loop()
    try:
        results = await loop.getaddrinfo(
            hostname,
            None,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise WebhookURLValidationError(
            url=hostname,
            reason=f"DNS resolution failed for '{hostname}': {exc}",
        ) from exc

    # getaddrinfo returns (family, type, proto, canonname, sockaddr).
    # sockaddr is (ip, port) for IPv4 or (ip, port, flowinfo, scope_id) for IPv6.
    seen: set[str] = set()
    ips: list[str] = []
    for _family, _type, _proto, _canonname, sockaddr in results:
        ip_str = str(sockaddr[0])
        if ip_str not in seen:
            seen.add(ip_str)
            ips.append(ip_str)
    return ips


async def validate_callback_url(url: str, *, require_https: bool = True) -> None:
    """Validate a webhook callback URL against SSRF rules.

    Rejects non-HTTPS schemes (unless *require_https* is False), missing
    hostnames, private/loopback/link-local IP literals, and hostnames that
    resolve to any blocked IP range (anti DNS-rebinding).
    """
    parsed = urlparse(url)

    # --- Scheme check ---
    allowed = _ALLOWED_SCHEMES_STRICT if require_https else _ALLOWED_SCHEMES_RELAXED
    if parsed.scheme not in allowed:
        raise WebhookURLValidationError(
            url=url,
            reason=(
                f"Scheme '{parsed.scheme}' is not allowed. "
                f"Allowed schemes: {', '.join(sorted(allowed))}"
            ),
        )

    # --- Hostname presence ---
    hostname = parsed.hostname
    if not hostname:
        raise WebhookURLValidationError(
            url=url,
            reason="URL must include a hostname",
        )

    # --- IP literal check (skip DNS for raw IPs) ---
    if _is_ip_blocked(hostname):
        raise WebhookURLValidationError(
            url=url,
            reason=f"Host '{hostname}' resolves to a blocked address range (private/loopback/link-local)",
        )

    # --- DNS resolution check (anti DNS-rebinding) ---
    resolved_ips = await _resolve_hostname(hostname)
    for ip_str in resolved_ips:
        if _is_ip_blocked(ip_str):
            raise WebhookURLValidationError(
                url=url,
                reason=(
                    f"Host '{hostname}' resolved to blocked IP '{ip_str}' "
                    "(private/loopback/link-local)"
                ),
                details={"resolved_ips": resolved_ips},
            )

    logger.debug(
        "webhook.url_validated",
        url=url,
        resolved_ips=resolved_ips,
    )


async def validate_agent_endpoint_url(url: str, *, require_https: bool = True) -> None:
    """Validate HTTP or WebSocket agent endpoints before persisting to the registry.

    WebSocket schemes are rewritten to HTTP/HTTPS so the same SSRF rules as
    :func:`validate_callback_url` apply (matches IssueOps ``is_safe_endpoint_url``).
    """
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme in _WS_TO_HTTP_SCHEMES:
        url = urlunparse(parsed._replace(scheme=_WS_TO_HTTP_SCHEMES[scheme]))
    await validate_callback_url(url, require_https=require_https)


@dataclass(frozen=True, slots=True)
class WebhookResult:
    url: str
    status_code: int
    success: bool
    elapsed_ms: float
    error: str | None = None


class WebhookDelivery:
    """Delivers webhook payloads via POST to registered callback URLs.

    Validates callback URLs to prevent SSRF, signs payloads with HMAC-SHA256,
    and supports configurable timeouts.

    Example:
        >>> import asyncio
        >>> delivery = WebhookDelivery(secret=b"my-secret", require_https=False)
        >>> result = asyncio.run(delivery.deliver(
        ...     "http://receiver.example.com/hook",
        ...     {"event": "task.completed", "task_id": "abc-123"},
        ... ))
        >>> print(result.success, result.status_code)
    """

    def __init__(
        self,
        *,
        secret: bytes | None = None,
        timeout_seconds: float = DEFAULT_WEBHOOK_TIMEOUT,
        require_https: bool = True,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._secret = secret
        self._timeout_seconds = timeout_seconds
        self._require_https = require_https
        self._external_client = client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def validate_url(self, url: str) -> None:
        await validate_callback_url(url, require_https=self._require_https)

    async def deliver(
        self,
        url: str,
        payload: dict[str, Any],
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> WebhookResult:
        await self.validate_url(url)

        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._secret is not None:
            headers[X_ASAP_SIGNATURE_HEADER] = compute_signature(body, self._secret)
        if extra_headers:
            headers.update(extra_headers)

        start = time.monotonic()
        try:
            if self._external_client is not None:
                response = await self._external_client.post(url, content=body, headers=headers)
            else:
                async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                    response = await client.post(url, content=body, headers=headers)

            elapsed_ms = (time.monotonic() - start) * 1000
            success = 200 <= response.status_code < 300

            logger.info(
                "webhook.delivered",
                url=url,
                status_code=response.status_code,
                success=success,
                elapsed_ms=round(elapsed_ms, 1),
            )

            return WebhookResult(
                url=url,
                status_code=response.status_code,
                success=success,
                elapsed_ms=round(elapsed_ms, 1),
            )

        except httpx.TimeoutException as exc:
            elapsed_ms = (time.monotonic() - start) * 1000
            logger.warning(
                "webhook.timeout",
                url=url,
                timeout_seconds=self._timeout_seconds,
                elapsed_ms=round(elapsed_ms, 1),
            )
            return WebhookResult(
                url=url,
                status_code=0,
                success=False,
                elapsed_ms=round(elapsed_ms, 1),
                error=f"Timeout after {self._timeout_seconds}s: {exc}",
            )

        except httpx.HTTPError as exc:
            elapsed_ms = (time.monotonic() - start) * 1000
            logger.warning(
                "webhook.delivery_failed",
                url=url,
                error=str(exc),
                elapsed_ms=round(elapsed_ms, 1),
            )
            return WebhookResult(
                url=url,
                status_code=0,
                success=False,
                elapsed_ms=round(elapsed_ms, 1),
                error=str(exc),
            )


# ------------------------------------------------------------------
# Signature helpers
# ------------------------------------------------------------------


def compute_signature(body: bytes, secret: bytes) -> str:
    """HMAC-SHA256 of body with secret; returns ``sha256=<hex>`` (GitHub/Stripe convention)."""
    digest = hmac.new(secret, body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_signature(body: bytes, secret: bytes, signature: str) -> bool:
    """Constant-time check that signature matches HMAC-SHA256 of body."""
    expected = compute_signature(body, secret)
    return hmac.compare_digest(expected, signature)


# ------------------------------------------------------------------
# Retry policy & constants
# ------------------------------------------------------------------

DEFAULT_MAX_RETRIES = 5
"""Maximum number of retry attempts before sending to the DLQ."""

DEFAULT_RETRY_BASE_DELAY = 1.0
"""Base delay in seconds for exponential backoff (1s → 2s → 4s → 8s → 16s)."""

DEFAULT_RETRY_MAX_DELAY = 16.0
"""Maximum delay cap in seconds for exponential backoff."""

DEFAULT_WEBHOOK_RATE_PER_SECOND = 10.0
"""Default per-URL webhook delivery rate (token bucket)."""

# Status codes that should NOT be retried (client errors — the request is wrong).
_NON_RETRYABLE_STATUS_RANGE = range(400, 500)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Webhook retry: max_retries, base_delay, max_delay, rate_per_second."""

    max_retries: int = DEFAULT_MAX_RETRIES
    base_delay: float = DEFAULT_RETRY_BASE_DELAY
    max_delay: float = DEFAULT_RETRY_MAX_DELAY
    rate_per_second: float = DEFAULT_WEBHOOK_RATE_PER_SECOND

    def backoff_delay(self, attempt: int) -> float:
        """min(base_delay * 2^attempt, max_delay) seconds."""
        return float(min(self.base_delay * (2**attempt), self.max_delay))


@dataclass(frozen=True, slots=True)
class DeadLetterEntry:
    """Permanently failed webhook: url, payload, last_result, attempts, created_at."""

    url: str
    payload: dict[str, Any]
    last_result: WebhookResult
    attempts: int
    created_at: float = field(default_factory=time.time)


# ------------------------------------------------------------------
# Per-URL token bucket (reuses pattern from WebSocketTokenBucket)
# ------------------------------------------------------------------


class _URLTokenBucket:
    """Per-URL token bucket (single event loop, not thread-safe)."""

    __slots__ = ("_rate", "_capacity", "_tokens", "_last_refill")

    def __init__(self, rate: float, capacity: float | None = None) -> None:
        if rate <= 0:
            raise ValueError("rate must be positive")
        self._rate = float(rate)
        self._capacity = float(capacity if capacity is not None else rate)
        self._tokens = self._capacity
        self._last_refill = time.monotonic()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
        self._last_refill = now

    def consume(self) -> bool:
        """Try to consume one token. Returns True if allowed, False if rate limited."""
        self._refill()
        if self._tokens >= 1:
            self._tokens -= 1
            return True
        return False

    def seconds_until_available(self) -> float:
        """Seconds until at least one token is available."""
        self._refill()
        if self._tokens >= 1:
            return 0.0
        return (1.0 - self._tokens) / self._rate


# ------------------------------------------------------------------
# Retry manager
# ------------------------------------------------------------------

# Type alias for the optional dead-letter callback.
DeadLetterCallback = Callable[[DeadLetterEntry], Coroutine[Any, Any, None]]


class WebhookRetryManager:
    """Delivers webhooks with automatic retry, exponential backoff, per-URL rate limiting, and DLQ.

    Wraps a ``WebhookDelivery`` instance and adds reliability:
        * **Retry queue** — failed deliveries are re-queued automatically.
        * **Exponential backoff** — delays double per attempt (1 → 2 → 4 → 8 → 16 s).
        * **Dead letter queue** — after ``max_retries`` the entry is logged and the
          optional ``on_dead_letter`` callback is invoked.
        * **Per-URL rate limit** — token bucket prevents flooding a single receiver.

    Example:
        >>> manager = WebhookRetryManager(delivery, policy=RetryPolicy(max_retries=3))
        >>> result = await manager.deliver_with_retry(url, payload)
    """

    def __init__(
        self,
        delivery: WebhookDelivery,
        *,
        policy: RetryPolicy | None = None,
        on_dead_letter: DeadLetterCallback | None = None,
    ) -> None:
        self._delivery = delivery
        self._policy = policy or RetryPolicy()
        self._on_dead_letter = on_dead_letter
        self._dead_letters: deque[DeadLetterEntry] = deque(maxlen=MAX_DEAD_LETTERS)
        self._url_buckets: dict[str, _URLTokenBucket] = {}
        self._max_buckets = 10_000

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def dead_letters(self) -> list[DeadLetterEntry]:
        return list(self._dead_letters)

    async def deliver_with_retry(
        self,
        url: str,
        payload: dict[str, Any],
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> WebhookResult:
        """Deliver with retry on 5xx/network error; no retry on 4xx; DLQ after max_retries."""
        last_result: WebhookResult | None = None

        for attempt in range(1 + self._policy.max_retries):
            # Per-URL rate limiting — wait until a token is available.
            await self._wait_for_rate_limit(url)

            last_result = await self._delivery.deliver(
                url,
                payload,
                extra_headers=extra_headers,
            )

            if last_result.success:
                return last_result

            # 4xx → do not retry (client error, the request itself is wrong).
            if last_result.status_code in _NON_RETRYABLE_STATUS_RANGE:
                logger.info(
                    "webhook.retry.non_retryable",
                    url=url,
                    status_code=last_result.status_code,
                    attempt=attempt + 1,
                )
                return last_result

            # Last attempt — don't sleep, go straight to DLQ.
            if attempt == self._policy.max_retries:
                break

            delay = self._policy.backoff_delay(attempt)
            logger.info(
                "webhook.retry.backoff",
                url=url,
                attempt=attempt + 1,
                max_retries=self._policy.max_retries,
                delay_seconds=delay,
                status_code=last_result.status_code,
                error=last_result.error,
            )
            await asyncio.sleep(delay)

        # All retries exhausted → dead letter queue.
        if last_result is None:
            # Defensive: the loop above always assigns ``last_result`` on at least one attempt.
            # Raising here rather than asserting survives ``python -O``.
            raise RuntimeError("webhook retry loop produced no result")
        await self._send_to_dead_letter(url, payload, last_result)
        return last_result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_bucket(self, url: str) -> _URLTokenBucket:
        bucket = self._url_buckets.get(url)
        if bucket is None:
            # Evict oldest entry if at capacity to prevent unbounded growth.
            if len(self._url_buckets) >= self._max_buckets:
                oldest_key = next(iter(self._url_buckets))
                del self._url_buckets[oldest_key]
                logger.debug(
                    "webhook.bucket_evicted",
                    evicted_url=oldest_key,
                    capacity=self._max_buckets,
                )
            bucket = _URLTokenBucket(rate=self._policy.rate_per_second)
            self._url_buckets[url] = bucket
        return bucket

    async def _wait_for_rate_limit(self, url: str) -> None:
        bucket = self._get_bucket(url)
        while not bucket.consume():
            wait = bucket.seconds_until_available()
            logger.debug(
                "webhook.rate_limit.waiting",
                url=url,
                wait_seconds=round(wait, 3),
            )
            await asyncio.sleep(wait)

    async def _send_to_dead_letter(
        self,
        url: str,
        payload: dict[str, Any],
        last_result: WebhookResult,
    ) -> None:
        entry = DeadLetterEntry(
            url=url,
            payload=payload,
            last_result=last_result,
            attempts=1 + self._policy.max_retries,
        )
        self._dead_letters.append(entry)
        logger.warning(
            "webhook.dead_letter",
            url=url,
            attempts=entry.attempts,
            last_status_code=last_result.status_code,
            last_error=last_result.error,
        )
        if self._on_dead_letter is not None:
            try:
                await self._on_dead_letter(entry)
            except Exception as err:
                logger.exception(
                    "webhook.dead_letter.callback_error",
                    url=url,
                    error=str(err),
                )
