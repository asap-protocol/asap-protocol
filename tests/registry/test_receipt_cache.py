"""Tests for bounded TTL registration receipt cache."""

from __future__ import annotations

import time

import pytest

from asap.registry.receipt_cache import (
    RegistrationReceiptTTLCache,
    create_registration_receipt_cache,
)


@pytest.fixture
def mock_monotonic_clock(monkeypatch: pytest.MonkeyPatch) -> dict[str, float]:
    """Return a mutable clock dict patched into ``time.monotonic`` for deterministic TTL tests."""
    clock: dict[str, float] = {"t": 0.0}
    monkeypatch.setattr(
        "asap.registry.receipt_cache.time.monotonic",
        lambda: clock["t"],
    )
    return clock


def test_ttl_cache_basic_set_get() -> None:
    """Set and get a value within TTL."""
    c: RegistrationReceiptTTLCache[str] = RegistrationReceiptTTLCache(maxsize=10, ttl=60.0)
    c["a"] = "one"
    assert c["a"] == "one"
    assert "a" in c


def test_ttl_cache_expires() -> None:
    """Entries disappear after TTL when using real monotonic time."""
    c: RegistrationReceiptTTLCache[str] = RegistrationReceiptTTLCache(maxsize=10, ttl=0.01)
    c["k"] = "v"
    time.sleep(0.03)
    assert "k" not in c
    with pytest.raises(KeyError):
        _ = c["k"]


def test_ttl_cache_evicts_when_full() -> None:
    """When at capacity, inserting a new key evicts the least-recently-used entry."""
    c: RegistrationReceiptTTLCache[int] = RegistrationReceiptTTLCache(maxsize=2, ttl=3600.0)
    c["a"] = 1
    c["b"] = 2
    c["c"] = 3
    assert "a" not in c
    assert "b" in c
    assert "c" in c


def test_factory_returns_empty_cache() -> None:
    """Factory produces an empty cache with default sizing."""
    c: RegistrationReceiptTTLCache[object] = create_registration_receipt_cache()
    assert len(c) == 0


def test_ttl_cache_rejects_invalid_maxsize() -> None:
    """``maxsize`` must be at least 1."""
    with pytest.raises(ValueError, match="maxsize must be >= 1"):
        RegistrationReceiptTTLCache(maxsize=0)


def test_ttl_cache_rejects_non_positive_ttl() -> None:
    """TTL must be strictly positive."""
    with pytest.raises(ValueError, match="ttl must be > 0"):
        RegistrationReceiptTTLCache(ttl=0.0)


def test_ttl_cache_getitem_raises_when_entry_expired(
    mock_monotonic_clock: dict[str, float],
) -> None:
    """Expired entries removed on read without relying on ``time.sleep``."""
    c: RegistrationReceiptTTLCache[str] = RegistrationReceiptTTLCache(maxsize=10, ttl=10.0)
    c["k"] = "v"
    mock_monotonic_clock["t"] = 25.0
    with pytest.raises(KeyError):
        _ = c["k"]


def test_ttl_cache_contains_false_for_non_string_keys() -> None:
    """``__contains__`` only applies to string keys; other types are false."""
    c: RegistrationReceiptTTLCache[str] = RegistrationReceiptTTLCache(maxsize=10, ttl=60.0)
    assert (42 in c) is False


def test_ttl_cache_contains_drops_expired_entry(mock_monotonic_clock: dict[str, float]) -> None:
    """``in`` prunes expired entries so stale keys do not appear present."""
    c: RegistrationReceiptTTLCache[str] = RegistrationReceiptTTLCache(maxsize=10, ttl=10.0)
    c["k"] = "v"
    mock_monotonic_clock["t"] = 25.0
    assert "k" not in c


def test_ttl_cache_delitem_removes_key() -> None:
    """``del`` removes a key and updates length."""
    c: RegistrationReceiptTTLCache[str] = RegistrationReceiptTTLCache(maxsize=10, ttl=60.0)
    c["a"] = "1"
    del c["a"]
    assert "a" not in c
    assert len(c) == 0


def test_ttl_cache_setitem_replaces_existing_key(mock_monotonic_clock: dict[str, float]) -> None:
    """Re-setting a key updates value and refreshes LRU / expiry bookkeeping."""
    c: RegistrationReceiptTTLCache[str] = RegistrationReceiptTTLCache(maxsize=10, ttl=60.0)
    c["k"] = "first"
    mock_monotonic_clock["t"] = 5.0
    c["k"] = "second"
    assert c["k"] == "second"


def test_getitem_removes_expired_entry_behind_still_valid_head(
    mock_monotonic_clock: dict[str, float],
) -> None:
    """Head may still be valid while a tail entry is expired (LRU order)."""
    c: RegistrationReceiptTTLCache[str] = RegistrationReceiptTTLCache(maxsize=10, ttl=100.0)
    mock_monotonic_clock["t"] = 0.0
    c["k1"] = "a"
    mock_monotonic_clock["t"] = 10.0
    c["k2"] = "b"
    mock_monotonic_clock["t"] = 11.0
    _ = c["k1"]
    mock_monotonic_clock["t"] = 102.0
    with pytest.raises(KeyError):
        _ = c["k1"]


def test_getitem_refreshes_lru_so_the_read_key_survives_eviction() -> None:
    """A successful read moves that key to the newest slot before the next insert."""
    cache: RegistrationReceiptTTLCache[str] = RegistrationReceiptTTLCache(maxsize=2, ttl=60.0)
    cache["cold"] = "1"
    cache["hot"] = "2"
    assert cache["cold"] == "1"
    cache["incoming"] = "3"
    assert "hot" not in cache
    assert cache["cold"] == "1"
    assert cache["incoming"] == "3"


def test_iter_yields_cache_keys() -> None:
    """Iteration yields current non-expired keys in LRU order."""
    c: RegistrationReceiptTTLCache[str] = RegistrationReceiptTTLCache(maxsize=10, ttl=60.0)
    c["a"] = "1"
    c["b"] = "2"
    assert list(c) == ["a", "b"]
