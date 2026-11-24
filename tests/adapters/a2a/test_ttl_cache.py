"""The bounded cache behind the JWKS cache and the ingest cache."""

from __future__ import annotations

import pytest

from asap.adapters.a2a._ttl_cache import BoundedTTLCache


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_an_entry_lives_for_its_lifetime_and_is_then_dropped() -> None:
    clock = _Clock()
    cache: BoundedTTLCache[str] = BoundedTTLCache(ttl_seconds=10, max_entries=4, clock=clock)
    cache.put("a", "one")

    clock.now = 9.9
    assert cache.get("a") == "one"
    clock.now = 10
    assert (cache.get("a"), len(cache)) == (None, 0)


def test_a_full_cache_drops_expired_entries_before_the_oldest_live_one() -> None:
    clock = _Clock()
    cache: BoundedTTLCache[int] = BoundedTTLCache(ttl_seconds=10, max_entries=2, clock=clock)
    cache.put("old", 1)
    clock.now = 5
    cache.put("live", 2)
    clock.now = 11

    cache.put("new", 3)

    assert (cache.get("live"), cache.get("new"), cache.get("old"), len(cache)) == (2, 3, None, 2)


@pytest.mark.parametrize(("ttl_seconds", "max_entries"), [(0, 1), (-1, 1), (1, 0)])
def test_bounds_must_be_positive(ttl_seconds: float, max_entries: int) -> None:
    with pytest.raises(ValueError, match="ttl_seconds"):
        BoundedTTLCache[int](ttl_seconds=ttl_seconds, max_entries=max_entries)
