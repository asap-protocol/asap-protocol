"""Host JWT replay window.

``create_host_jwt`` mints Host JWTs valid for 300 s, but the replay cache kept a
``jti`` for 90 s by default, so a Host JWT was accepted again between the end of
that window and its ``exp``. ``verify_host_jwt`` now asks the cache to keep the
record until ``exp`` plus the 60 s ``iat`` skew. Host JWTs have no lifetime cap,
so that time is at most 420 s after now: the default TTL plus twice the skew.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from joserfc import jwt as jose_jwt

from asap.auth.agent_jwt import (
    DEFAULT_HOST_JWT_TTL_SECONDS,
    HOST_JWT_TYP,
    HOST_PUBLIC_KEY_CLAIM,
    JWT_ALGS_SIGN,
    JtiReplayCache,
    JwtVerifyResult,
    _ed25519_private_key_to_okp_key,
    create_host_jwt,
    verify_host_jwt,
)
from asap.auth.identity import InMemoryHostStore, jwk_thumbprint_sha256
from asap.auth.jti_replay_cache import JtiReplayCacheProtocol, RedisJtiReplayCache

_NOW = 2_000_000_000
_AUDIENCE = "urn:asap:agent:receiver"
_REPLAY_ERROR = "jti replay detected"
_EXPIRED_ERROR = "token expired or missing exp"
# The clock skew allowed on ``iat``; a replay record outlives ``exp`` by it.
_SKEW_SECONDS = 60
# The longest a record is kept: a default-TTL token from a clock that runs the
# whole skew fast, plus the skew margin after its ``exp``.
_LONGEST_RECORD_SECONDS = DEFAULT_HOST_JWT_TTL_SECONDS + 2 * _SKEW_SECONDS

pytestmark = pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")


@pytest.fixture(autouse=True)
def _frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.time", lambda: float(_NOW))


def _fakeredis_cache() -> RedisJtiReplayCache:
    fakeredis = pytest.importorskip("fakeredis")
    return RedisJtiReplayCache(fakeredis.FakeStrictRedis(decode_responses=False))


_DEFAULT_CACHES = [
    pytest.param(JtiReplayCache, id="memory"),
    pytest.param(_fakeredis_cache, id="redis"),
]


def _signed(host_key: Ed25519PrivateKey, *, exp: float) -> str:
    """A Host JWT signed by ``host_key``, issued now, with the given ``exp``."""
    signing_key = _ed25519_private_key_to_okp_key(host_key)
    host_public = dict(signing_key.as_dict(private=False))
    claims: dict[str, Any] = {
        "iss": jwk_thumbprint_sha256(host_public),
        "aud": _AUDIENCE,
        "iat": _NOW,
        "exp": exp,
        "jti": "jwt_host",
        HOST_PUBLIC_KEY_CLAIM: host_public,
    }
    header = {"alg": JWT_ALGS_SIGN, "typ": HOST_JWT_TYP}
    return jose_jwt.encode(header, claims, signing_key, algorithms=[JWT_ALGS_SIGN])


def _user(
    token: str, cache: JtiReplayCacheProtocol, monkeypatch: pytest.MonkeyPatch
) -> Callable[..., Awaitable[JwtVerifyResult]]:
    """Return ``use_at(now, record_jti=True)``: verify ``token`` at ``now`` against ``cache``."""
    hosts = InMemoryHostStore()

    async def use_at(now: float, *, record_jti: bool = True) -> JwtVerifyResult:
        monkeypatch.setattr("time.time", lambda: now)
        return await verify_host_jwt(
            token,
            hosts,
            expected_audience=_AUDIENCE,
            jti_replay_cache=cache,
            record_jti=record_jti,
        )

    return use_at


def test_the_default_host_jwt_ttl_outlives_the_default_window() -> None:
    assert DEFAULT_HOST_JWT_TTL_SECONDS == 300


@pytest.mark.parametrize("make_cache", _DEFAULT_CACHES)
@pytest.mark.parametrize("record_jti", [True, False], ids=["recording route", "read-only poll"])
async def test_refuses_a_second_use_after_the_default_window_until_exp(
    make_cache: Callable[[], JtiReplayCacheProtocol],
    record_jti: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = make_cache()
    token = create_host_jwt(Ed25519PrivateKey.generate(), _AUDIENCE)
    exp = _NOW + DEFAULT_HOST_JWT_TTL_SECONDS
    use_at = _user(token, cache, monkeypatch)

    first = await use_at(_NOW)
    second = await use_at(exp - 1, record_jti=record_jti)  # past the 90 s window, before exp

    assert (first.ok, first.error) == (True, None)
    assert (second.ok, second.error) == (False, _REPLAY_ERROR)


@pytest.mark.parametrize("make_cache", _DEFAULT_CACHES)
async def test_keeps_the_record_until_exp_plus_the_skew(
    make_cache: Callable[[], JtiReplayCacheProtocol], monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = make_cache()
    token = create_host_jwt(Ed25519PrivateKey.generate(), _AUDIENCE)
    exp = _NOW + DEFAULT_HOST_JWT_TTL_SECONDS
    use_at = _user(token, cache, monkeypatch)

    first = await use_at(_NOW)
    assert first.claims is not None
    iss, jti = first.claims["iss"], first.claims["jti"]

    assert (await use_at(exp + 1)).error == _EXPIRED_ERROR
    monkeypatch.setattr("time.time", lambda: float(exp + _SKEW_SECONDS - 1))
    assert cache.check_and_record(iss, jti) is False
    monkeypatch.setattr("time.time", lambda: float(exp + _SKEW_SECONDS + 1))
    assert cache.contains(iss, jti) is False
    assert cache.check_and_record(iss, jti) is True


@pytest.mark.parametrize("make_cache", _DEFAULT_CACHES)
@pytest.mark.parametrize(
    "exp",
    [_NOW + 3600, 1e300, float("inf")],
    ids=["an hour", "past the Redis expire range", "Infinity"],
)
async def test_keeps_the_record_at_most_420_seconds_for_a_longer_exp(
    make_cache: Callable[[], JtiReplayCacheProtocol],
    exp: float,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = make_cache()
    use_at = _user(_signed(Ed25519PrivateKey.generate(), exp=exp), cache, monkeypatch)

    first = await use_at(_NOW)
    second = await use_at(_NOW + _LONGEST_RECORD_SECONDS - 1)

    assert (first.ok, first.error) == (True, None)
    assert (second.ok, second.error) == (False, _REPLAY_ERROR)
    assert first.claims is not None
    monkeypatch.setattr("time.time", lambda: float(_NOW + _LONGEST_RECORD_SECONDS + 1))
    assert cache.contains(first.claims["iss"], first.claims["jti"]) is False


@pytest.mark.parametrize("forwards_kwargs", [False, True], ids=["two arguments", "**kwargs"])
async def test_calls_a_cache_without_keep_until_with_two_arguments(
    forwards_kwargs: bool, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    class TwoArgumentCache:
        """A cache written before ``keep_until``: it keeps its own window."""

        def __init__(self) -> None:
            self.recorded: set[tuple[str, str]] = set()

        def contains(self, partition_key: str, jti: str) -> bool:
            return (partition_key, jti) in self.recorded

        def check_and_record(self, partition_key: str, jti: str) -> bool:
            first_use = (partition_key, jti) not in self.recorded
            self.recorded.add((partition_key, jti))
            return first_use

    class ForwardingCache(TwoArgumentCache):
        """A wrapper whose ``**kwargs`` reach the two-argument method."""

        def check_and_record(self, *args: Any, **kwargs: Any) -> bool:
            return super().check_and_record(*args, **kwargs)

    cache = ForwardingCache() if forwards_kwargs else TwoArgumentCache()
    use_at = _user(create_host_jwt(Ed25519PrivateKey.generate(), _AUDIENCE), cache, monkeypatch)

    with caplog.at_level("WARNING"):
        results = [await use_at(_NOW), await use_at(_NOW), await use_at(_NOW, record_jti=False)]

    assert [(r.ok, r.error) for r in results] == [
        (True, None),
        (False, _REPLAY_ERROR),
        (False, _REPLAY_ERROR),
    ]
    warned = [
        r.msg["cache_class"] for r in caplog.records
        if isinstance(r.msg, dict) and r.msg["event"] == "asap.auth.jti_cache_without_keep_until"
    ]  # fmt: skip
    assert warned == [type(cache).__qualname__]
