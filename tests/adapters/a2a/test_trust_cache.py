"""The JWKS cache of the card key resolver: bounded lifetime and size.

Random ``kid`` values must not turn into requests: a cached key set answers
every ``kid`` until it expires. No test touches the network.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519
from joserfc.jwk import OKPKey

from asap.adapters.a2a import A2AKeyResolutionError, A2AKeyResolver, A2ATrustConfig, AllowedJku

_JKUS = [f"https://keys{n}.example.com/jwks.json" for n in range(3)]
_KEY = {
    **OKPKey.import_key(ed25519.Ed25519PrivateKey.generate().public_key()).as_dict(),
    "kid": "k1",
}


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _CountingJwksServer:
    """Serves ``{"keys": [k1]}`` on every host, or ``status`` when it is not 200."""

    def __init__(self) -> None:
        self.hosts: list[str] = []
        self.status = 200
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.hosts.append(request.headers["Host"])
        return httpx.Response(self.status, json={"keys": [_KEY]})


async def _public_dns(hostname: str) -> list[str]:
    return ["93.184.216.34"]


def _resolver(server: _CountingJwksServer, clock: _Clock, **config: Any) -> A2AKeyResolver:
    allowlist = {jku: AllowedJku(agents=["urn:asap:agent:research"]) for jku in _JKUS}
    trust = A2ATrustConfig(jku_allowlist=allowlist, **config)
    return A2AKeyResolver(trust, dns_resolver=_public_dns, transport=server.transport, clock=clock)


async def test_a_fetched_jwks_is_reused_until_its_lifetime_ends() -> None:
    server, clock = _CountingJwksServer(), _Clock()
    resolver = _resolver(server, clock, jwks_cache_ttl_seconds=60)

    assert await resolver("k1", _JKUS[0]) is not None
    clock.now += 59
    assert await resolver("k1", _JKUS[0]) is not None
    assert len(server.hosts) == 1

    clock.now += 1
    assert await resolver("k1", _JKUS[0]) is not None
    assert len(server.hosts) == 2


async def test_random_unknown_kids_never_refetch_a_cached_jwks() -> None:
    server = _CountingJwksServer()
    resolver = _resolver(server, _Clock())

    found = [await resolver(uuid.uuid4().hex, _JKUS[0]) for _ in range(50)]

    assert found == [None] * 50
    assert len(server.hosts) == 1


async def test_the_cache_holds_at_most_its_maximum_entries_and_drops_the_oldest() -> None:
    server = _CountingJwksServer()
    resolver = _resolver(server, _Clock(), jwks_cache_max_entries=2)

    for jku in [*_JKUS, _JKUS[2], _JKUS[1], _JKUS[0]]:
        await resolver("k1", jku)

    assert server.hosts == [
        "keys0.example.com", "keys1.example.com", "keys2.example.com", "keys0.example.com"
    ]  # fmt: skip


async def test_concurrent_lookups_of_one_jku_make_one_request() -> None:
    server = _CountingJwksServer()
    resolver = _resolver(server, _Clock())

    keys = await asyncio.gather(*(resolver("k1", _JKUS[0]) for _ in range(5)))

    assert all(key is not None for key in keys)
    assert len(server.hosts) == 1


async def test_a_failed_fetch_is_not_cached() -> None:
    server = _CountingJwksServer()
    resolver = _resolver(server, _Clock())
    server.status = 503

    with pytest.raises(A2AKeyResolutionError):
        await resolver("k1", _JKUS[0])
    server.status = 200

    assert await resolver("k1", _JKUS[0]) is not None
    assert len(server.hosts) == 2


@pytest.mark.parametrize(
    "settings",
    [
        {"jwks_cache_ttl_seconds": 0},
        {"jwks_cache_ttl_seconds": 86401},
        {"jwks_cache_max_entries": 0},
        {"jwks_cache_max_entries": 1025},
    ],
)
def test_the_cache_bounds_are_bounded(settings: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="jwks_cache"):
        A2ATrustConfig(**settings)
