"""Tests for Redis-backed JTI replay cache."""

from __future__ import annotations

import importlib.util
import sys
from typing import TYPE_CHECKING, Callable, Protocol

import pytest

from asap.auth.agent_jwt import JtiReplayCache
from asap.auth.jti_replay_cache import JtiReplayCacheProtocol, RedisJtiReplayCache

if TYPE_CHECKING:
    from _pytest.monkeypatch import MonkeyPatch


class _ReplayCacheUnderTest(Protocol):
    def contains(self, partition_key: str, jti: str) -> bool: ...

    def check_and_record(self, partition_key: str, jti: str) -> bool: ...


def _assert_shared_replay_semantics(
    cache: _ReplayCacheUnderTest,
    monkeypatch: MonkeyPatch,
) -> None:
    """Exercise TTL, replay rejection, and blank-jti handling."""
    t0 = 50_000.0
    monkeypatch.setattr("asap.auth.agent_jwt.time.time", lambda: t0)
    assert not cache.contains("part", "jti-1")
    assert cache.check_and_record("part", "jti-1")
    assert not cache.check_and_record("part", "jti-1")
    assert cache.contains("part", "jti-1")
    assert not cache.check_and_record("part", "")
    assert not cache.check_and_record("part", "   ")


@pytest.mark.parametrize(
    "factory",
    [
        pytest.param(lambda: JtiReplayCache(ttl_seconds=1.0), id="memory"),
    ],
)
def test_jti_replay_cache_shared_semantics(
    factory: Callable[[], _ReplayCacheUnderTest],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In-memory cache satisfies the shared replay contract."""
    _assert_shared_replay_semantics(factory(), monkeypatch)


def test_redis_jti_replay_cache_from_url_raises_when_redis_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``from_url`` surfaces the optional ``redis`` extra requirement."""
    monkeypatch.setitem(sys.modules, "redis", None)
    with pytest.raises(ImportError, match="asap-protocol\\[redis\\]"):
        RedisJtiReplayCache.from_url("redis://localhost:6379/0")


def _fakeredis_cache() -> RedisJtiReplayCache | None:
    if importlib.util.find_spec("redis") is None:
        return None
    if importlib.util.find_spec("fakeredis") is None:
        return None
    import fakeredis

    client = fakeredis.FakeStrictRedis(decode_responses=False)
    return RedisJtiReplayCache(client, ttl_seconds=1.0)


@pytest.mark.skipif(_fakeredis_cache() is None, reason="redis/fakeredis not installed")
def test_redis_jti_replay_cache_shared_semantics(monkeypatch: pytest.MonkeyPatch) -> None:
    """Redis cache matches in-memory replay semantics."""
    cache = _fakeredis_cache()
    assert cache is not None
    _assert_shared_replay_semantics(cache, monkeypatch)


def test_jti_replay_partitions_do_not_share_a_jti() -> None:
    """The same jti is first-use in each partition and a replay only inside its own."""
    cache = JtiReplayCache(ttl_seconds=90.0)
    assert cache.check_and_record("host-a", "shared-jti") is True
    assert cache.check_and_record("host-b", "shared-jti") is True
    assert cache.check_and_record("host-a", "shared-jti") is False
    assert cache.contains("host-b", "shared-jti") is True
    assert cache.contains("host-a", "other-jti") is False


@pytest.mark.skipif(_fakeredis_cache() is None, reason="redis/fakeredis not installed")
def test_redis_jti_replay_partitions_do_not_share_a_jti() -> None:
    """Redis keys include the partition, so hosts do not consume each other's jti."""
    cache = _fakeredis_cache()
    assert cache is not None
    assert cache.check_and_record("host-a", "shared-jti") is True
    assert cache.check_and_record("host-b", "shared-jti") is True
    assert cache.check_and_record("host-a", "shared-jti") is False
    assert cache.contains("host-b", "shared-jti") is True


def test_jti_replay_cache_protocol_accepts_memory() -> None:
    """Structural protocol typing covers the default in-memory backend."""
    memory: JtiReplayCacheProtocol = JtiReplayCache()
    assert isinstance(memory, JtiReplayCacheProtocol)


@pytest.mark.skipif(_fakeredis_cache() is None, reason="redis/fakeredis not installed")
def test_jti_replay_cache_protocol_accepts_redis() -> None:
    """Structural protocol typing covers the Redis backend."""
    cache = _fakeredis_cache()
    assert cache is not None
    assert isinstance(cache, JtiReplayCacheProtocol)
