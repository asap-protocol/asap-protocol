"""A small cache with a lifetime per entry and a maximum number of entries.

Used for fetched JWKS and for verified Agent Cards: data fetched from a URL
that an untrusted party may influence must not stay forever or grow without
bound. Not thread-safe; it is used from one event loop.
"""

from __future__ import annotations

import time
from collections.abc import Callable


class BoundedTTLCache[V]:
    """Keep each entry for ``ttl_seconds``; when full, drop expired entries, then the oldest.

    Example:
        >>> cache: BoundedTTLCache[int] = BoundedTTLCache(ttl_seconds=60, max_entries=2)
        >>> cache.put("a", 1)
        >>> cache.get("a")
        1
    """

    def __init__(
        self,
        *,
        ttl_seconds: float,
        max_entries: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0 or max_entries < 1:
            raise ValueError(
                f"ttl_seconds must be > 0 and max_entries >= 1, got {ttl_seconds}, {max_entries}"
            )
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._clock = clock
        self._entries: dict[str, tuple[float, V]] = {}

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, key: str) -> V | None:
        """Return the live value for *key*, or ``None``; an expired entry is dropped."""
        entry = self._entries.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if self._clock() >= expires_at:
            del self._entries[key]
            return None
        return value

    def put(self, key: str, value: V) -> None:
        """Store *value* for *key* with a fresh lifetime, evicting to stay within the bound."""
        self._entries.pop(key, None)
        if len(self._entries) >= self._max_entries:
            now = self._clock()
            for stale in [k for k, (expires_at, _) in self._entries.items() if now >= expires_at]:
                del self._entries[stale]
        while len(self._entries) >= self._max_entries:
            del self._entries[next(iter(self._entries))]
        self._entries[key] = (self._clock() + self._ttl_seconds, value)
