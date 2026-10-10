"""Rate limiting for ASAP protocol transport layer.

This module provides:
    - **HTTP rate limiting**: ``ASAPRateLimiter`` wraps the ``limits`` package to enforce
      per-key (IP/sender) rate limits on FastAPI endpoints. Replaces the deprecated
      ``slowapi`` dependency to eliminate ``asyncio.iscoroutinefunction`` warnings
      (Python 3.12+).
    - **WebSocket rate limiting**: ``WebSocketTokenBucket`` provides a per-connection
      token bucket for message rate limiting after the HTTP upgrade.

HTTP rate limiting storage:
    Default is ``memory://`` (per-process). In multi-worker deployments (e.g. Gunicorn
    with 4 workers) the effective rate is approximately limit × number of workers.
    For shared limits across workers, set **ASAP_RATE_LIMIT_BACKEND** to a Redis URI:
    ``ASAP_RATE_LIMIT_BACKEND=redis://localhost:6379/0``. Requires the optional
    dependency: ``pip install 'asap-protocol[redis]'``.

Public exports:
    ASAPRateLimiter: HTTP rate limiter using ``limits`` package.
    RateLimitExceeded: Exception raised when a rate limit is exceeded.
    WebSocketTokenBucket: Per-connection token bucket for WebSocket messages.
    DEFAULT_WS_MESSAGES_PER_SECOND: Default WebSocket message rate.
    create_limiter: Factory for production rate limiters.
    create_test_limiter: Factory for test rate limiters (isolated storage).
"""

from __future__ import annotations

import os
import time
import uuid  # noqa: I001 -- used only for slowapi ``memory://{hex}`` namespace suffix; domain entity IDs use asap.models.ids.generate_id
from collections.abc import Callable, Sequence

from fastapi import Request
from limits import RateLimitItem, parse_many
from limits.aio.storage.base import Storage as AioStorage
from limits.storage import Storage, storage_from_string
from limits.strategies import MovingWindowRateLimiter

from asap.observability import get_logger

logger = get_logger(__name__)

# Default rate limit: burst + sustained (token bucket pattern).
DEFAULT_RATE_LIMIT = "10/second;100/minute"


class RateLimitExceeded(Exception):
    """Raised when a rate limit is exceeded.

    Drop-in replacement for ``slowapi.errors.RateLimitExceeded``.

    Attributes:
        detail: Human-readable description of the exceeded limit.
        retry_after: Seconds until the client may retry.
        limit: String representation of the limit that was hit.
    """

    def __init__(
        self,
        detail: str,
        *,
        retry_after: int = 60,
        limit: str = "",
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.retry_after = retry_after
        self.limit = limit


class ASAPRateLimiter:
    """HTTP rate limiter backed by the ``limits`` package.

    Replaces ``slowapi.Limiter`` with a simpler, deprecation-free implementation.
    Each instance owns its own ``Storage`` so multiple app instances have
    independent counters (unless a shared Redis URI is provided).

    Example:
        >>> limiter = ASAPRateLimiter(
        ...     key_func=lambda req: req.client.host,
        ...     limits=["10/second;100/minute"],
        ... )
        >>> # In a FastAPI route:
        >>> limiter.check(request)  # raises RateLimitExceeded on 429
    """

    def __init__(
        self,
        *,
        key_func: Callable[[Request], str],
        limits: list[str] | None = None,
        storage_uri: str | None = None,
    ) -> None:
        self._key_func = key_func

        limit_strings = limits or [DEFAULT_RATE_LIMIT]
        self._rate_limits: list[RateLimitItem] = []
        for limit_str in limit_strings:
            self._rate_limits.extend(parse_many(limit_str))

        if storage_uri is None:
            storage_uri = f"memory://{uuid.uuid4().hex}"

        self._storage: Storage | AioStorage = storage_from_string(storage_uri)
        self._strategy = MovingWindowRateLimiter(self._storage)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(self, request: Request) -> None:
        """Raise ``RateLimitExceeded`` if *request* violates any configured limit.

        Two-phase approach: test all limits (read-only) first, then hit all
        counters only if every limit passes. Prevents double-counting drift.
        """
        key = self._key_func(request)

        # Phase 1: Test all limits without consuming hits.
        for rate_limit in self._rate_limits:
            if not self._strategy.test(rate_limit, key):
                window_stats = self._strategy.get_window_stats(rate_limit, key)
                # window_stats[0] is the reset timestamp (epoch seconds).
                retry_seconds = max(1, int(window_stats[0] - time.time()))
                raise RateLimitExceeded(
                    detail=f"Rate limit exceeded: {rate_limit}",
                    retry_after=retry_seconds,
                    limit=str(rate_limit),
                )

        # Phase 2: All limits pass — now increment all counters.
        for rate_limit in self._rate_limits:
            self._strategy.hit(rate_limit, key)

    def check_n(self, request: Request, n: int) -> None:
        """Raise ``RateLimitExceeded`` if *n* hits would violate any configured limit.

        Used for JSON-RPC batch requests where each sub-request consumes one hit.
        """
        if n <= 0:
            return
        key = self._key_func(request)
        for rate_limit in self._rate_limits:
            if not self._strategy.test(rate_limit, key, cost=n):
                window_stats = self._strategy.get_window_stats(rate_limit, key)
                retry_seconds = max(1, int(window_stats[0] - time.time()))
                raise RateLimitExceeded(
                    detail=f"Rate limit exceeded: {rate_limit}",
                    retry_after=retry_seconds,
                    limit=str(rate_limit),
                )
        for rate_limit in self._rate_limits:
            if not self._strategy.hit(rate_limit, key, cost=n):
                window_stats = self._strategy.get_window_stats(rate_limit, key)
                retry_seconds = max(1, int(window_stats[0] - time.time()))
                raise RateLimitExceeded(
                    detail=f"Rate limit exceeded: {rate_limit}",
                    retry_after=retry_seconds,
                    limit=str(rate_limit),
                )

    def test(self, request: Request) -> bool:
        """Return True if *request* would pass all limits (no counter increment)."""
        key = self._key_func(request)
        return all(self._strategy.test(rate_limit, key) for rate_limit in self._rate_limits)

    @property
    def limits(self) -> list[RateLimitItem]:
        return list(self._rate_limits)


# ------------------------------------------------------------------
# Factory functions (backward-compatible with previous slowapi API)
# ------------------------------------------------------------------


def create_limiter(
    limits: Sequence[str] | None = None,
    *,
    key_func: Callable[[Request], str] | None = None,
    storage_uri: str | None = None,
) -> ASAPRateLimiter:
    """Create a production rate limiter.

    Storage is chosen by **storage_uri** (explicit argument) or by the
    **ASAP_RATE_LIMIT_BACKEND** environment variable. If neither is set,
    uses in-memory storage (per-process; effective limit × workers in
    multi-worker deployments). Set ASAP_RATE_LIMIT_BACKEND=redis://host:port/db
    for shared limits across workers (requires ``pip install 'asap-protocol[redis]'``).

    Returns a fully independent instance with its own storage.
    """
    if key_func is None:
        key_func = get_remote_address

    if limits is None:
        limits = [DEFAULT_RATE_LIMIT]

    if storage_uri is None:
        storage_uri = (os.environ.get("ASAP_RATE_LIMIT_BACKEND") or "").strip() or None

    if storage_uri is None:
        storage_uri = f"memory://{uuid.uuid4().hex}"
        logger.warning(
            "asap.rate_limit.memory_storage",
            message=(
                "memory:// storage is per-process; in multi-worker deployments "
                "(e.g. Gunicorn), effective rate = limit × workers. Set "
                "ASAP_RATE_LIMIT_BACKEND=redis://... for shared limits."
            ),
        )
    elif storage_uri.strip().lower().startswith("redis://"):
        try:
            import redis  # noqa: F401
        except ImportError:
            logger.error(
                "asap.rate_limit.redis_missing",
                message="Redis storage requested but 'redis' package is not installed.",
            )
            raise ImportError(
                "Redis support requires the 'redis' package. "
                "Install it with: pip install 'asap-protocol[redis]'"
            ) from None
        logger.info(
            "asap.rate_limit.redis_storage",
            message="Rate limit storage backend: Redis (shared across workers).",
        )

    return ASAPRateLimiter(
        key_func=key_func,
        limits=list(limits),
        storage_uri=storage_uri,
    )


def create_test_limiter(
    limits: Sequence[str] | None = None,
    *,
    key_func: Callable[[Request], str] | None = None,
) -> ASAPRateLimiter:
    """Create a rate limiter for testing with isolated ``memory://`` storage."""
    if key_func is None:
        key_func = get_remote_address

    if limits is None:
        limits = ["100000/minute"]

    return ASAPRateLimiter(
        key_func=key_func,
        limits=list(limits),
        storage_uri=f"memory://test-{uuid.uuid4().hex}",
    )


def get_remote_address(request: Request) -> str:
    if request.client is not None:
        return str(request.client.host)
    return "127.0.0.1"


def registration_token_key(request: Request) -> str:
    """Rate-limit key: SHA-256 of Bearer token, else client IP fallback."""
    import hashlib

    auth = request.headers.get("Authorization") or ""
    if auth.startswith("Bearer "):
        token = auth[7:].strip().encode()
        if token:
            return f"regtok:{hashlib.sha256(token).hexdigest()}"
    return f"ip:{get_remote_address(request)}"


# Registration endpoint budget (AUTO-003): 5 POSTs per token per hour.
REGISTRATION_RATE_LIMIT = "5/hour"


def create_registration_rate_limiter(
    *,
    storage_uri: str | None = None,
) -> ASAPRateLimiter:
    """Limiter for ``POST /registry/agents`` — isolated storage recommended in tests."""
    if storage_uri is None:
        storage_uri = f"memory://{uuid.uuid4().hex}"
    return ASAPRateLimiter(
        key_func=registration_token_key,
        limits=[REGISTRATION_RATE_LIMIT],
        storage_uri=storage_uri,
    )


# ------------------------------------------------------------------
# WebSocket token bucket
# ------------------------------------------------------------------

DEFAULT_WS_MESSAGES_PER_SECOND = 10.0


class WebSocketTokenBucket:
    """Per-connection token bucket: refill at rate/sec, one token per message. Not thread-safe."""

    __slots__ = ("_rate", "_capacity", "_tokens", "_last_refill")

    def __init__(
        self,
        rate: float = DEFAULT_WS_MESSAGES_PER_SECOND,
        capacity: float | None = None,
    ) -> None:
        if rate <= 0:
            raise ValueError("rate must be positive")
        self._rate = float(rate)
        self._capacity = float(capacity if capacity is not None else rate)
        self._tokens = self._capacity
        self._last_refill = time.monotonic()

    def _refill(self) -> None:
        """First call may grant full capacity if bucket was created long before first message."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(
            self._capacity,
            self._tokens + elapsed * self._rate,
        )
        self._last_refill = now

    def consume(self, n: int = 1) -> bool:
        if n <= 0:
            return True
        self._refill()
        if self._tokens >= n:
            self._tokens -= n
            return True
        return False

    @property
    def rate(self) -> float:
        return self._rate


__all__ = [
    # HTTP rate limiting
    "ASAPRateLimiter",
    "RateLimitExceeded",
    "DEFAULT_RATE_LIMIT",
    "REGISTRATION_RATE_LIMIT",
    "create_limiter",
    "create_registration_rate_limiter",
    "create_test_limiter",
    "get_remote_address",
    "registration_token_key",
    # WebSocket rate limiting
    "DEFAULT_WS_MESSAGES_PER_SECOND",
    "WebSocketTokenBucket",
]
