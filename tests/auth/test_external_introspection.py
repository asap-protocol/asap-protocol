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
from tests.auth.local_issuer import LocalIssuer, nested_payload_token, unsigned_token

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
        self.raw_answer: bytes | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.raw_answer is not None:
            return httpx.Response(self.status, content=self.raw_answer)
        return httpx.Response(self.status, json=self.answer)

    def verifier(
        self, *extra_issuers: LocalIssuer, **endpoint_overrides: Any
    ) -> ExternalTokenVerifier:
        endpoint = {"url": f"{_ISSUER}/introspect", "client_id": "rs", "client_secret": _SECRET}
        endpoint.update(endpoint_overrides)
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


@pytest.mark.parametrize(("overrides", "ttl"), [({}, 60), ({"cache_ttl_seconds": 10}, 10)])
async def test_revoked_token_works_until_the_cache_ttl_then_fails(
    endpoint: _FakeIntrospectionEndpoint,
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, Any],
    ttl: int,
) -> None:
    # Before the per-issuer cap the answer was cached until
    # 30 s before exp, so a revoked token kept working for most of its life.
    clock = [time.time()]
    monkeypatch.setattr("asap.auth.introspection.time.time", lambda: clock[0])
    verifier = endpoint.verifier(**overrides)
    await verifier.verify(_TOKEN)
    endpoint.answer = {"active": False}

    clock[0] += ttl - 1
    assert (await verifier.verify(_TOKEN)).subject == "user-9"
    clock[0] += 2
    await _refused(verifier, _TOKEN, Reason.TOKEN_INACTIVE)
    assert len(endpoint.requests) == 2


async def test_cache_ttl_of_zero_asks_on_every_request(
    endpoint: _FakeIntrospectionEndpoint,
) -> None:
    verifier = endpoint.verifier(cache_ttl_seconds=0)
    await verifier.verify(_TOKEN)
    await verifier.verify(_TOKEN)
    assert len(endpoint.requests) == 2


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


async def test_too_deeply_nested_answer_is_unavailable_not_a_crash(
    endpoint: _FakeIntrospectionEndpoint,
) -> None:
    endpoint.raw_answer = b'{"active": ' + b"[" * 100_000 + b"]" * 100_000 + b"}"
    await _refused(endpoint.verifier(), _TOKEN, Reason.KEY_SOURCE_UNAVAILABLE)


async def test_jwt_of_the_introspection_issuer_is_introspected(
    endpoint: _FakeIntrospectionEndpoint,
) -> None:
    idp = LocalIssuer(_ISSUER)
    token = idp.mint(idp.claims(_AUDIENCE))
    assert (await endpoint.verifier().verify(token)).subject == "user-9"
    assert parse_qs(endpoint.requests[0].content.decode())["token"] == [token]


def _never_sent_tokens() -> dict[str, tuple[str, Reason]]:
    idp, other = LocalIssuer(_ISSUER), LocalIssuer("https://other.example")
    agent_jwt = idp.mint(idp.claims(_AUDIENCE), header={"typ": "agent+jwt"})
    other_jwt = other.mint(other.claims(_AUDIENCE))
    header, payload, _ = other_jwt.split(".")
    jwe_header = unsigned_token({"alg": "RSA-OAEP-256", "enc": "A256GCM"}, {}).split(".")[0]
    return {
        "agent+jwt": (agent_jwt, Reason.TOKEN_TYPE_REJECTED),
        "host+jwt": (idp.mint(idp.claims(_AUDIENCE), header={"typ": "host+jwt"}), Reason.TOKEN_TYPE_REJECTED),
        "another issuer": (other_jwt, Reason.UNTRUSTED_ISSUER),
        # JOSE-shaped but not a three-segment JWS: these were sent before the
        # malformed-token check.
        "a JWT plus a dot": (other_jwt + ".", Reason.MALFORMED_TOKEN),
        "an agent+jwt plus a dot": (agent_jwt + ".", Reason.MALFORMED_TOKEN),
        "header and payload, one dot": (f"{header}.{payload}", Reason.MALFORMED_TOKEN),
        "a JWE, five segments": (f"{jwe_header}.a2V5.aXY.Y2lwaGVy.dGFn", Reason.MALFORMED_TOKEN),
        "a JOSE header alone": (header, Reason.MALFORMED_TOKEN),
        "JSON nested too deeply to read": (nested_payload_token({}).split(".")[1] + ".x", Reason.MALFORMED_TOKEN),
    }  # fmt: skip


@pytest.mark.parametrize("name", list(_never_sent_tokens()))
async def test_asap_tokens_other_issuers_and_jose_shapes_are_never_sent(
    endpoint: _FakeIntrospectionEndpoint, name: str
) -> None:
    token, reason = _never_sent_tokens()[name]
    await _refused(endpoint.verifier(), token, reason)
    assert endpoint.requests == []


@pytest.mark.parametrize("token", ["ory_at_x8Yb2Q.k9JdW1", "a.b", "opaque.with.three.dots"])
async def test_dotted_opaque_tokens_are_still_introspected(
    endpoint: _FakeIntrospectionEndpoint, token: str
) -> None:
    # The first segment is not base64url JSON, so nothing marks these as JOSE.
    assert (await endpoint.verifier().verify(token)).subject == "user-9"
    assert parse_qs(endpoint.requests[0].content.decode())["token"] == [token]


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


@pytest.mark.parametrize("header_padding", [0, 600])
async def test_three_segment_tokens_are_never_sent(
    endpoint: _FakeIntrospectionEndpoint, header_padding: int
) -> None:
    # joserfc refuses a JWS header over 512 bytes (an x5c chain, say); such a
    # token from another issuer must not reach the introspection issuer.
    other = LocalIssuer("https://other.example")
    header = {"x5u": "https://other.example/" + "c" * header_padding}
    token = other.mint(other.claims(_AUDIENCE), header=header)
    reason = Reason.UNTRUSTED_ISSUER if header_padding == 0 else Reason.MALFORMED_TOKEN
    await _refused(endpoint.verifier(), token, reason)
    await _refused(endpoint.verifier(), "a.b.c", Reason.MALFORMED_TOKEN)
    assert endpoint.requests == []
