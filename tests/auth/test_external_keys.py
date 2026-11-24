"""Signing keys of external issuers: sources, guard, cache, refresh limit.

No test touches the network: the resolver and the httpx transport are fakes,
so every remote fetch still runs through ``guarded_fetch_json``.
"""

from __future__ import annotations

import asyncio
import secrets
from typing import Any

import httpx
import pytest

from asap.auth.external_keys import (
    EXTERNAL_JWKS_CACHE_TTL_SECONDS,
    EXTERNAL_JWKS_MIN_REFETCH_SECONDS,
    IssuerKeySource,
)
from asap.auth.external_tokens import (
    ExternalIssuerConfig,
    ExternalTokenError,
    ExternalTokenErrorReason,
)
from asap.utils.guarded_fetch import AddressResolver
from tests.auth.local_issuer import LocalIssuer

_ISSUER = "https://idp.example"
_JWKS_URI = "https://keys.idp.example/jwks.json"
_JWKS_SOURCE = {"kind": "jwks_uri", "jwks_uri": _JWKS_URI}
_HMAC_KEY = {"kty": "oct", "kid": "hmac", "k": "c2VjcmV0LWtleS1mb3ItaG1hYy1vbmx5"}


async def _public_resolver(hostname: str) -> list[str]:
    return ["93.184.216.34"]


class _FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _FakeIdp:
    """Serves discovery and the JWKS (plus ``extra_keys``); records each path."""

    def __init__(self, issuer: LocalIssuer) -> None:
        self.issuer = issuer
        self.paths: list[str] = []
        self.fail = False
        self.clock = _FakeClock()
        self.discovery: dict[str, Any] = {"issuer": _ISSUER, "jwks_uri": _JWKS_URI}
        self.extra_keys: list[dict[str, Any]] = []
        self.serve_issuer_key = True

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        if self.fail:
            return httpx.Response(500)
        if request.url.path == "/.well-known/openid-configuration":
            return httpx.Response(200, json=self.discovery)
        issuer_keys = self.issuer.jwks["keys"] if self.serve_issuer_key else []
        return httpx.Response(200, json={"keys": [*self.extra_keys, *issuer_keys]})

    def source(
        self,
        key_source: dict[str, Any] = _JWKS_SOURCE,
        resolver: AddressResolver = _public_resolver,
    ) -> IssuerKeySource:
        entry = self.issuer.config_entry("https://api.example/asap", key_source=key_source)
        return IssuerKeySource(
            ExternalIssuerConfig.model_validate(entry),
            resolver=resolver,
            transport=httpx.MockTransport(self),
            clock=self.clock,
        )


@pytest.fixture
def idp() -> _FakeIdp:
    return _FakeIdp(LocalIssuer(_ISSUER))


async def _unavailable(source: IssuerKeySource, kid: str | None) -> None:
    with pytest.raises(ExternalTokenError) as caught:
        await source.keys_for(kid)
    assert caught.value.reason is ExternalTokenErrorReason.KEY_SOURCE_UNAVAILABLE


async def test_static_source_never_fetches(idp: _FakeIdp) -> None:
    source = idp.source({"kind": "static_jwks", "jwks": idp.issuer.jwks})
    keys = await source.keys_for(secrets.token_hex(8))
    assert [key.kid for key in keys.keys] == [idp.issuer.kid]
    assert idp.paths == []


async def test_remote_keys_are_cached_for_a_bounded_lifetime(idp: _FakeIdp) -> None:
    source = idp.source()
    for _ in range(3):
        keys = await source.keys_for(idp.issuer.kid)
    assert [key.kid for key in keys.keys] == [idp.issuer.kid]
    assert idp.paths == ["/jwks.json"]

    idp.clock.now += EXTERNAL_JWKS_CACHE_TTL_SECONDS
    await source.keys_for(idp.issuer.kid)
    assert idp.paths == ["/jwks.json", "/jwks.json"]


async def test_unknown_kid_refreshes_once_for_a_rotated_key(idp: _FakeIdp) -> None:
    source = idp.source()
    await source.keys_for(idp.issuer.kid)
    idp.issuer.rotate_key()
    idp.clock.now += EXTERNAL_JWKS_MIN_REFETCH_SECONDS

    keys = await source.keys_for(idp.issuer.kid)
    assert [key.kid for key in keys.keys] == [idp.issuer.kid]
    assert len(idp.paths) == 2


async def test_random_kids_cannot_turn_requests_into_fetches(idp: _FakeIdp) -> None:
    source = idp.source()
    await source.keys_for(idp.issuer.kid)
    idp.clock.now += EXTERNAL_JWKS_MIN_REFETCH_SECONDS

    await asyncio.gather(*(source.keys_for(secrets.token_hex(8)) for _ in range(50)))
    assert len(idp.paths) == 2, "one refresh per interval, however many unknown kids arrive"

    idp.clock.now += EXTERNAL_JWKS_MIN_REFETCH_SECONDS - 1
    await source.keys_for(secrets.token_hex(8))
    assert len(idp.paths) == 2


async def test_failed_fetch_is_not_retried_within_the_interval(idp: _FakeIdp) -> None:
    source = idp.source()
    idp.fail = True
    await _unavailable(source, idp.issuer.kid)
    await _unavailable(source, idp.issuer.kid)
    assert len(idp.paths) == 1

    idp.fail = False
    idp.clock.now += EXTERNAL_JWKS_MIN_REFETCH_SECONDS
    await source.keys_for(idp.issuer.kid)
    assert len(idp.paths) == 2


async def test_failed_refresh_keeps_the_current_keys(idp: _FakeIdp) -> None:
    source = idp.source()
    await source.keys_for(idp.issuer.kid)
    idp.fail = True
    idp.clock.now += EXTERNAL_JWKS_MIN_REFETCH_SECONDS

    keys = await source.keys_for(secrets.token_hex(8))
    assert [key.kid for key in keys.keys] == [idp.issuer.kid]


async def test_private_jwks_host_is_refused_before_any_request(idp: _FakeIdp) -> None:
    async def private_resolver(hostname: str) -> list[str]:
        return ["10.0.0.7"]

    await _unavailable(idp.source(resolver=private_resolver), idp.issuer.kid)
    assert idp.paths == []


async def test_symmetric_keys_in_a_remote_jwks_are_dropped(idp: _FakeIdp) -> None:
    idp.extra_keys = [_HMAC_KEY]
    keys = await idp.source().keys_for("hmac")
    assert [key.kid for key in keys.keys] == [idp.issuer.kid]


async def test_jwks_without_an_asymmetric_key_is_unavailable(idp: _FakeIdp) -> None:
    idp.extra_keys, idp.serve_issuer_key = [_HMAC_KEY], False
    await _unavailable(idp.source(), "hmac")


async def test_too_deeply_nested_jwks_is_unavailable_not_a_crash(idp: _FakeIdp) -> None:
    deep = b'{"keys": ' + b"[" * 100_000 + b"]" * 100_000 + b"}"
    entry = idp.issuer.config_entry("https://api.example/asap", key_source=_JWKS_SOURCE)
    source = IssuerKeySource(
        ExternalIssuerConfig.model_validate(entry),
        resolver=_public_resolver,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=deep)),
    )
    await _unavailable(source, idp.issuer.kid)


async def test_oidc_discovery_finds_the_jwks_uri(idp: _FakeIdp) -> None:
    keys = await idp.source({"kind": "oidc_discovery"}).keys_for(idp.issuer.kid)
    assert [key.kid for key in keys.keys] == [idp.issuer.kid]
    assert idp.paths == ["/.well-known/openid-configuration", "/jwks.json"]


@pytest.mark.parametrize(
    "discovery",
    [
        {"issuer": "https://evil.example", "jwks_uri": _JWKS_URI},
        {"issuer": _ISSUER},
        {"issuer": _ISSUER, "jwks_uri": "https://169.254.169.254/jwks"},
        {"issuer": _ISSUER, "jwks_uri": "http://keys.idp.example/jwks.json"},
    ],
)
async def test_unusable_discovery_document_is_refused(
    idp: _FakeIdp, discovery: dict[str, Any]
) -> None:
    idp.discovery = discovery
    await _unavailable(idp.source({"kind": "oidc_discovery"}), idp.issuer.kid)
    assert idp.paths == ["/.well-known/openid-configuration"]
