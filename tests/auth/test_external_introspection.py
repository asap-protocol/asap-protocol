"""Introspection path for opaque external tokens (RFC 7662).

The single introspection issuer gets opaque tokens and its own JWTs; the
request goes through the guarded POST. The response is checked like verified
JWT claims: ``active``, ``iss`` if present, ``aud``, ``exp``, ``nbf``, ``sub``.
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from asap.auth.external_tokens import (
    ExternalTokenConfig,
    ExternalTokenError,
    ExternalTokenErrorReason,
)
from asap.auth.external_verifier import ExternalTokenVerifier
from tests.auth.local_issuer import LocalIssuer

_ISSUER = "https://idp.example"
_AUDIENCE = "https://api.example/asap"
_SECRET = "client-s3cret-value"
_TOKEN = "opaque-access-token-value"
Reason = ExternalTokenErrorReason


async def _public_resolver(hostname: str) -> list[str]:
    return ["93.184.216.34"]


class _FakeIntrospectionEndpoint:
    """Answers RFC 7662 requests with ``answer``; records each request."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.status = 200
        self.answer: dict[str, Any] = {
            "active": True,
            "iss": _ISSUER,
            "sub": "user-9",
            "aud": _AUDIENCE,
            "scope": "files:read",
            "client_id": "mcp-client",
            "exp": int(time.time()) + 600,
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, json=self.answer)

    def verifier(self, *extra_issuers: LocalIssuer) -> ExternalTokenVerifier:
        endpoint = {"url": f"{_ISSUER}/introspect", "client_id": "rs", "client_secret": _SECRET}
        rules = [{"scope": "files:read", "capability": "file.read"}]
        entry = {"issuer": _ISSUER, "audience": _AUDIENCE, "introspection": endpoint}
        issuers = [{**entry, "grant_rules": rules}]
        issuers += [issuer.config_entry(_AUDIENCE) for issuer in extra_issuers]
        config = ExternalTokenConfig.model_validate({"issuers": issuers})
        transport = httpx.MockTransport(self)
        return ExternalTokenVerifier(config, resolver=_public_resolver, transport=transport)


@pytest.fixture
def endpoint() -> _FakeIntrospectionEndpoint:
    return _FakeIntrospectionEndpoint()


async def _refused(verifier: ExternalTokenVerifier, token: str, reason: Reason) -> None:
    with pytest.raises(ExternalTokenError) as caught:
        await verifier.verify(token)
    assert caught.value.reason is reason
    assert token not in str(caught.value) and _SECRET not in str(caught.value)


async def test_active_opaque_token_gives_a_principal(endpoint: _FakeIntrospectionEndpoint) -> None:
    principal = await endpoint.verifier().verify(_TOKEN)

    assert principal.key == (_ISSUER, "user-9")
    assert principal.client_id == "mcp-client"
    assert [grant.capability for grant in principal.grants] == ["file.read"]
    [request] = endpoint.requests
    assert request.method == "POST" and request.url.path == "/introspect"
    assert parse_qs(request.content.decode())["token"] == [_TOKEN]
    assert request.headers["Authorization"].startswith("Basic ")
    assert _TOKEN not in str(request.url) and _SECRET not in str(request.url)


async def test_same_token_used_twice_succeeds_and_is_cached(
    endpoint: _FakeIntrospectionEndpoint,
) -> None:
    verifier = endpoint.verifier()
    assert await verifier.verify(_TOKEN) == await verifier.verify(_TOKEN)
    assert len(endpoint.requests) == 1


@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        ({"active": False}, Reason.TOKEN_INACTIVE),
        ({"aud": "https://other.example"}, Reason.AUDIENCE_MISMATCH),
        ({"aud": None}, Reason.AUDIENCE_MISMATCH),
        ({"exp": int(time.time()) - 600}, Reason.TOKEN_EXPIRED),
        ({"exp": None}, Reason.MISSING_CLAIM),
        ({"nbf": int(time.time()) + 600}, Reason.TOKEN_NOT_YET_VALID),
        ({"iss": "https://evil.example"}, Reason.UNTRUSTED_ISSUER),
        ({"sub": None}, Reason.MISSING_CLAIM),
    ],
)
async def test_refused_introspection_answer(
    endpoint: _FakeIntrospectionEndpoint, answer: dict[str, Any], reason: Reason
) -> None:
    merged = {**endpoint.answer, **answer}
    endpoint.answer = {key: value for key, value in merged.items() if value is not None}
    await _refused(endpoint.verifier(), _TOKEN, reason)


async def test_endpoint_failure_is_key_source_unavailable(
    endpoint: _FakeIntrospectionEndpoint,
) -> None:
    endpoint.status = 500
    await _refused(endpoint.verifier(), _TOKEN, Reason.KEY_SOURCE_UNAVAILABLE)


async def test_jwt_of_the_introspection_issuer_is_introspected(
    endpoint: _FakeIntrospectionEndpoint,
) -> None:
    idp = LocalIssuer(_ISSUER)
    token = idp.mint(idp.claims(_AUDIENCE))
    assert (await endpoint.verifier().verify(token)).subject == "user-9"
    assert parse_qs(endpoint.requests[0].content.decode())["token"] == [token]


@pytest.mark.parametrize("typ_or_issuer", ["agent+jwt", "host+jwt", "https://other.example"])
async def test_asap_tokens_and_other_issuers_are_never_sent(
    endpoint: _FakeIntrospectionEndpoint, typ_or_issuer: str
) -> None:
    if typ_or_issuer.startswith("https://"):
        other = LocalIssuer(typ_or_issuer)
        token, reason = other.mint(other.claims(_AUDIENCE)), Reason.UNTRUSTED_ISSUER
    else:
        idp = LocalIssuer(_ISSUER)
        token = idp.mint(idp.claims(_AUDIENCE), header={"typ": typ_or_issuer})
        reason = Reason.TOKEN_TYPE_REJECTED
    await _refused(endpoint.verifier(), token, reason)
    assert endpoint.requests == []


async def test_jwt_issuer_next_to_the_introspection_issuer_still_verifies_locally(
    endpoint: _FakeIntrospectionEndpoint,
) -> None:
    partner = LocalIssuer("https://partner.example")
    principal = await endpoint.verifier(partner).verify(partner.mint(partner.claims(_AUDIENCE)))
    assert principal.issuer == partner.issuer
    assert endpoint.requests == []


async def test_jws_with_a_bad_payload_is_malformed_and_never_sent(
    endpoint: _FakeIntrospectionEndpoint,
) -> None:
    idp = LocalIssuer(_ISSUER)
    header, _, signature = idp.mint(idp.claims(_AUDIENCE)).split(".")
    await _refused(endpoint.verifier(), f"{header}.WzFd.{signature}", Reason.MALFORMED_TOKEN)
    assert endpoint.requests == []
