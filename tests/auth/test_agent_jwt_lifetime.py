"""Agent JWT lifetime cap.

``verify_agent_jwt`` accepted any ``exp`` in the future, and the replay cache
recorded a ``jti`` for a fixed window (90 s by default). A captured token with
``exp`` a year ahead was accepted again once its record had expired. The cap is
:data:`AGENT_JWT_MAX_LIFETIME_SECONDS`: the 60 s TTL plus the 60 s ``iat`` skew,
counted from now and from ``iat``. Host JWTs keep their own TTL. A token within
the cap could still outlive the 90 s record, so the replay cache now keeps the
``jti`` until ``exp`` plus that skew when that is later than its window.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from joserfc import jwt as jose_jwt

from asap.auth.agent_jwt import (
    AGENT_JWT_MAX_LIFETIME_SECONDS,
    AGENT_JWT_TYP,
    JWT_ALGS_SIGN,
    JtiReplayCache,
    JwtVerifyResult,
    _ed25519_private_key_to_okp_key,
    create_agent_jwt,
    create_host_jwt,
    verify_agent_jwt,
    verify_host_jwt,
)
from asap.auth.identity import (
    AgentSession,
    HostIdentity,
    InMemoryAgentStore,
    InMemoryHostStore,
    jwk_thumbprint_sha256,
)
from asap.auth.jti_replay_cache import JtiReplayCacheProtocol, RedisJtiReplayCache
from tests.crypto.jwk_helpers import ed25519_public_jwk

_NOW = 2_000_000_000
_AUDIENCE = "https://mcp.example.com/mcp"
_LIFETIME_ERROR = "token lifetime too long"
_REPLAY_ERROR = "jti replay detected"
# The clock skew allowed on ``iat``; a replay record outlives ``exp`` by it.
_SKEW_SECONDS = 60

pytestmark = pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")


@pytest.fixture(autouse=True)
def _frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.time", lambda: float(_NOW))


async def _stores(
    agent_key: Ed25519PrivateKey, host_key: Ed25519PrivateKey
) -> tuple[InMemoryHostStore, InMemoryAgentStore]:
    """Persist one active host and one active agent bound to it."""
    created = datetime.now(timezone.utc)
    hosts = InMemoryHostStore()
    agents = InMemoryAgentStore()
    host_public = ed25519_public_jwk(host_key)
    await hosts.save(
        HostIdentity(
            host_id="h1",
            public_key=host_public,
            status="active",
            created_at=created,
            updated_at=created,
        )
    )
    await agents.save(
        AgentSession(
            agent_id="a1",
            host_id="h1",
            public_key=ed25519_public_jwk(agent_key),
            mode="delegated",
            status="active",
            created_at=created,
        )
    )
    return hosts, agents


def _signed(agent_key: Ed25519PrivateKey, host_key: Ed25519PrivateKey, **times: int | None) -> str:
    """An Agent JWT signed by ``agent_key`` with the given ``iat``/``exp`` (``iat`` omitted when None)."""
    claims: dict[str, Any] = {
        "iss": jwk_thumbprint_sha256(ed25519_public_jwk(host_key)),
        "sub": "a1",
        "aud": _AUDIENCE,
        "jti": f"jwt_{times}",
        **{name: value for name, value in times.items() if value is not None},
    }
    header = {"alg": JWT_ALGS_SIGN, "typ": AGENT_JWT_TYP}
    signing_key = _ed25519_private_key_to_okp_key(agent_key)
    return jose_jwt.encode(header, claims, signing_key, algorithms=[JWT_ALGS_SIGN])


async def _verify(token: str, agent_key: Ed25519PrivateKey, host_key: Ed25519PrivateKey) -> Any:
    hosts, agents = await _stores(agent_key, host_key)
    return await verify_agent_jwt(
        token, hosts, agents, expected_audience=_AUDIENCE, jti_replay_cache=JtiReplayCache()
    )


def test_the_cap_is_the_ttl_plus_the_iat_skew() -> None:
    assert AGENT_JWT_MAX_LIFETIME_SECONDS == 120


@pytest.mark.parametrize(
    ("iat", "exp"),
    [
        (_NOW, _NOW + 3600),
        (_NOW, _NOW + 365 * 86_400),
        (None, _NOW + 121),
        (_NOW - 100, _NOW + 21),
    ],
    ids=["an hour", "a year", "121 s ahead with no iat", "121 s after iat"],
)
async def test_refuses_a_token_that_lives_too_long_on_first_use(iat: int | None, exp: int) -> None:
    agent_key, host_key = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()

    result = await _verify(_signed(agent_key, host_key, iat=iat, exp=exp), agent_key, host_key)

    assert result.ok is False
    assert result.error == _LIFETIME_ERROR


@pytest.mark.parametrize(
    ("iat", "exp"),
    [(_NOW, _NOW + 60), (_NOW + 60, _NOW + 120), (_NOW, _NOW + 120), (None, _NOW + 120)],
    ids=["the 60 s TTL", "a clock 60 s fast", "120 s after iat", "120 s ahead with no iat"],
)
async def test_accepts_a_token_within_the_cap(iat: int | None, exp: int) -> None:
    agent_key, host_key = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()

    result = await _verify(_signed(agent_key, host_key, iat=iat, exp=exp), agent_key, host_key)

    assert result.ok is True


async def test_accepts_a_token_from_create_agent_jwt() -> None:
    agent_key, host_key = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    host_thumbprint = jwk_thumbprint_sha256(ed25519_public_jwk(host_key))
    token = create_agent_jwt(agent_key, host_thumbprint, "a1", _AUDIENCE)

    assert (await _verify(token, agent_key, host_key)).ok is True


async def test_does_not_cap_a_host_jwt() -> None:
    token = create_host_jwt(Ed25519PrivateKey.generate(), _AUDIENCE, ttl_seconds=3600)

    result = await verify_host_jwt(token, InMemoryHostStore(), expected_audience=_AUDIENCE)

    assert result.ok is True


def _fakeredis_cache() -> RedisJtiReplayCache:
    fakeredis = pytest.importorskip("fakeredis")
    return RedisJtiReplayCache(fakeredis.FakeStrictRedis(decode_responses=False))


_DEFAULT_CACHES = [
    pytest.param(JtiReplayCache, id="memory"),
    pytest.param(_fakeredis_cache, id="redis"),
]


@pytest.mark.parametrize("make_cache", _DEFAULT_CACHES)
async def test_refuses_a_replay_after_the_default_window_until_exp(
    make_cache: Callable[[], JtiReplayCacheProtocol], monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_key, host_key = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    hosts, agents = await _stores(agent_key, host_key)
    cache = make_cache()
    exp = _NOW + AGENT_JWT_MAX_LIFETIME_SECONDS
    token = _signed(agent_key, host_key, iat=_NOW, exp=exp)

    async def use_at(now: float) -> JwtVerifyResult:
        monkeypatch.setattr("time.time", lambda: now)
        return await verify_agent_jwt(
            token, hosts, agents, expected_audience=_AUDIENCE, jti_replay_cache=cache
        )

    first = await use_at(_NOW)
    replay = await use_at(exp - 1)  # past the default 90 s window, before exp

    assert first.ok is True
    assert (replay.ok, replay.error) == (False, _REPLAY_ERROR)
    assert first.claims is not None
    monkeypatch.setattr("time.time", lambda: float(exp + _SKEW_SECONDS - 1))
    assert cache.contains("a1", first.claims["jti"]) is True


@pytest.mark.parametrize("make_cache", _DEFAULT_CACHES)
def test_keep_until_never_shortens_the_window(
    make_cache: Callable[[], JtiReplayCache | RedisJtiReplayCache],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = make_cache()

    assert cache.check_and_record("a1", "jwt_1", keep_until=_NOW + 10) is True
    monkeypatch.setattr("time.time", lambda: float(_NOW + 89))
    assert cache.check_and_record("a1", "jwt_1", keep_until=_NOW + 10) is False


@pytest.mark.parametrize("forwards_kwargs", [False, True], ids=["two arguments", "**kwargs"])
async def test_calls_a_cache_without_keep_until_with_two_arguments(
    forwards_kwargs: bool, caplog: pytest.LogCaptureFixture
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

    agent_key, host_key = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    hosts, agents = await _stores(agent_key, host_key)
    token = _signed(agent_key, host_key, iat=_NOW, exp=_NOW + 60)
    cache = ForwardingCache() if forwards_kwargs else TwoArgumentCache()

    with caplog.at_level("WARNING"):
        results = [
            await verify_agent_jwt(
                token, hosts, agents, expected_audience=_AUDIENCE, jti_replay_cache=cache
            )
            for _ in range(2)
        ]

    assert [(r.ok, r.error) for r in results] == [(True, None), (False, _REPLAY_ERROR)]
    warned = [
        r.msg["cache_class"] for r in caplog.records
        if isinstance(r.msg, dict) and r.msg["event"] == "asap.auth.jti_cache_without_keep_until"
    ]  # fmt: skip
    assert warned == [type(cache).__qualname__]
