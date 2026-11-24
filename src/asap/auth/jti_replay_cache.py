"""JWT ``jti`` replay protection backends (in-memory default, optional Redis).

Multi-instance deployments should inject :class:`RedisJtiReplayCache` via
``create_app(identity_jti_cache=...)`` or ``MCPAuthConfig.jti_replay_cache``
so replay state is shared across workers. Requires ``pip install 'asap-protocol[redis]'``.
"""

from __future__ import annotations

import functools
import inspect
import math
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from asap.observability import get_logger

if TYPE_CHECKING:
    from redis import Redis

logger = get_logger(__name__)

_KEEP_UNTIL = "keep_until"


@runtime_checkable
class JtiReplayCacheProtocol(Protocol):
    """Structural interface for ``jti`` replay guards.

    ``check_and_record`` may also take a keyword argument ``keep_until``: a
    time in seconds since the epoch until which the record must block reuse,
    even when the cache's own window ends sooner. :func:`check_and_record_until`
    passes it only to a method with a ``keep_until`` parameter, so a cache
    written for the two-argument form keeps working with its own window.
    """

    def contains(self, partition_key: str, jti: str) -> bool:
        """Return whether ``jti`` is still recorded for ``partition_key``."""
        ...

    def check_and_record(self, partition_key: str, jti: str) -> bool:
        """Record ``jti`` for ``partition_key``; return False on replay."""
        ...


def check_and_record_until(
    cache: JtiReplayCacheProtocol, partition_key: str, jti: str, *, keep_until: float
) -> bool:
    """Record ``jti`` and ask ``cache`` to keep the record at least until ``keep_until``.

    A cache whose ``check_and_record`` has no ``keep_until`` parameter (a
    ``**kwargs`` wrapper counts as none) is called with two arguments and keeps
    its own window, which may end before ``keep_until``. The first such call
    for each cache class logs ``asap.auth.jti_cache_without_keep_until``.

    Example:
        >>> check_and_record_until(cache, "agent-1", "jwt_abc", keep_until=token_exp + 60)
        True
    """
    record = cache.check_and_record
    if not _takes_keep_until(record):
        _warn_cache_without_keep_until(type(cache))
        return record(partition_key, jti)
    record_until: Callable[..., bool] = record
    return record_until(partition_key, jti, keep_until=keep_until)


def _takes_keep_until(record: Callable[..., bool]) -> bool:
    try:
        parameter = inspect.signature(record).parameters.get(_KEEP_UNTIL)
    except (TypeError, ValueError):
        return False
    return parameter is not None and parameter.kind in (
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.KEYWORD_ONLY,
    )


@functools.cache
def _warn_cache_without_keep_until(cache_class: type) -> None:
    """Log once per class: its records can expire while their tokens are still valid."""
    logger.warning("asap.auth.jti_cache_without_keep_until", cache_class=cache_class.__qualname__)


def _jti_blank(jti: str) -> bool:
    return not jti or not str(jti).strip()


def _redis_key(prefix: str, partition_key: str, jti: str) -> str:
    return f"{prefix}:{partition_key}:{jti}"


class RedisJtiReplayCache:
    """Redis-backed ``jti`` replay guard.

    Each key lives ``ttl_seconds`` (default 90 s), or until ``keep_until``
    when the caller passes a later one.

    Uses ``SET key 1 NX EX ttl`` for atomic first-use recording and ``EXISTS``
    for read-only replay checks (Host JWT polling routes).

    ``partition_key`` and ``jti`` are assumed URL-safe (no ``:``); keys are
    ``{prefix}:{partition_key}:{jti}``.

    Redis connection errors propagate to callers (same pattern as
    :mod:`asap.transport.rate_limit`); wrap with health checks or retries
    if you need graceful degradation.

    Example:
        >>> cache = RedisJtiReplayCache.from_url("redis://localhost:6379/0")
        >>> cache.check_and_record("host-thumbprint", "jwt_abc")
        True
    """

    def __init__(
        self,
        client: Redis,
        *,
        ttl_seconds: float = 90.0,
        key_prefix: str = "asap:jti-replay",
    ) -> None:
        self._client = client
        self._ttl = ttl_seconds
        self._key_prefix = key_prefix

    @classmethod
    def from_url(
        cls,
        url: str,
        *,
        ttl_seconds: float = 90.0,
        key_prefix: str = "asap:jti-replay",
        **redis_kwargs: Any,
    ) -> RedisJtiReplayCache:
        """Build a cache from a Redis URI (requires the optional ``redis`` extra)."""
        try:
            import redis
        except ImportError as exc:
            msg = (
                "Redis JTI replay cache requires the 'redis' package. "
                "Install it with: pip install 'asap-protocol[redis]'"
            )
            raise ImportError(msg) from exc
        client = redis.Redis.from_url(url, **redis_kwargs)
        return cls(client, ttl_seconds=ttl_seconds, key_prefix=key_prefix)

    def contains(self, partition_key: str, jti: str) -> bool:
        """Return whether ``jti`` is still recorded for ``partition_key``."""
        if _jti_blank(jti):
            return False
        key = _redis_key(self._key_prefix, partition_key, jti)
        return bool(self._client.exists(key))

    def check_and_record(
        self, partition_key: str, jti: str, *, keep_until: float | None = None
    ) -> bool:
        """Record ``jti`` for ``partition_key``.

        ``keep_until`` (seconds since the epoch) keeps the key at least until
        then when that is later than ``ttl_seconds`` from now.

        Returns:
            True on first use: no live record of ``(partition_key, jti)``.

            False if that record is still live (replay).
        """
        if _jti_blank(jti):
            return False
        key = _redis_key(self._key_prefix, partition_key, jti)
        # Floor fractional seconds to whole-second Redis EX (in-memory keeps float TTL);
        # round ``keep_until`` up so the key never expires before it.
        ttl_int = max(1, int(self._ttl))
        if keep_until is not None:
            ttl_int = max(ttl_int, math.ceil(keep_until - time.time()))
        return bool(self._client.set(key, "1", nx=True, ex=ttl_int))
