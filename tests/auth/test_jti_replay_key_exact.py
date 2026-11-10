"""Replay keys must be the exact partition and ``jti``.

A prefix, case, or padding check would treat a different token as a replay,
or let a real replay through when the stored key only shares a substring.
"""

from __future__ import annotations

import importlib.util

import pytest

from asap.auth.agent_jwt import JtiReplayCache
from asap.auth.jti_replay_cache import JtiReplayCacheProtocol, RedisJtiReplayCache

_EXACT_PARTITION = "host-01"
_EXACT_JTI = "jti-01"
_NEIGHBORS: tuple[tuple[str, str], ...] = (
    (_EXACT_PARTITION, "jti-010"),
    (_EXACT_PARTITION, "jti-0"),
    (_EXACT_PARTITION, "JTI-01"),
    (_EXACT_PARTITION, "jti-01 "),
    (_EXACT_PARTITION, " jti-01"),
    ("host-010", _EXACT_JTI),
    ("host-0", _EXACT_JTI),
    ("Host-01", _EXACT_JTI),
    ("host-01 ", _EXACT_JTI),
    (" host-01", _EXACT_JTI),
)


def _assert_replay_key_is_exact(cache: JtiReplayCacheProtocol) -> None:
    """Record one key, then require every neighbor to be a first use."""
    assert cache.check_and_record(_EXACT_PARTITION, _EXACT_JTI) is True
    assert cache.check_and_record(_EXACT_PARTITION, _EXACT_JTI) is False
    assert cache.contains(_EXACT_PARTITION, _EXACT_JTI) is True
    for partition, jti in _NEIGHBORS:
        assert cache.contains(partition, jti) is False
        assert cache.check_and_record(partition, jti) is True
    assert cache.check_and_record(_EXACT_PARTITION, _EXACT_JTI) is False


def test_memory_jti_replay_key_match_is_exact() -> None:
    """In-memory replay state isolates prefix, case, and padded keys."""
    _assert_replay_key_is_exact(JtiReplayCache(ttl_seconds=90.0))


def _fakeredis_cache() -> RedisJtiReplayCache | None:
    """Build a Redis replay cache, or None when the optional extra is absent.

    fakeredis is imported in this function so collection still works when the
    redis extra is not installed.
    """
    if importlib.util.find_spec("redis") is None or importlib.util.find_spec("fakeredis") is None:
        return None
    import fakeredis

    client = fakeredis.FakeStrictRedis(decode_responses=False)
    return RedisJtiReplayCache(client, ttl_seconds=90.0)


@pytest.mark.skipif(_fakeredis_cache() is None, reason="redis/fakeredis not installed")
def test_redis_jti_replay_key_match_is_exact() -> None:
    """Redis replay keys keep the same exact partition and ``jti`` boundary."""
    cache = _fakeredis_cache()
    assert cache is not None
    _assert_replay_key_is_exact(cache)
