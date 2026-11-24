"""JWT validation path for external access tokens (COMPAT-001).

Every refusal is checked for its typed reason and for a message that holds
neither the token nor its signature. The claim checks after the signature
are covered in ``test_external_claims.py``.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime, timezone
from unittest.mock import Mock

import httpx
import pytest
from joserfc import jwk

from asap.auth.external_tokens import (
    ExternalTokenConfig,
    ExternalTokenError,
    ExternalTokenErrorReason,
)
from asap.auth.external_verifier import ExternalTokenVerifier
from tests.auth.local_issuer import LocalIssuer, unsigned_token

_AUDIENCE = "https://api.example/asap"
_RULES = [{"scope": "files:read", "capability": "file.read"}]
Reason = ExternalTokenErrorReason
TokenFactory = Callable[[LocalIssuer], str]


@pytest.fixture
def idp_a() -> LocalIssuer:
    return LocalIssuer("https://idp-a.example")


@pytest.fixture
def idp_b() -> LocalIssuer:
    return LocalIssuer("https://idp-b.example", algorithm="EdDSA")


def _verifier(*issuers: LocalIssuer) -> ExternalTokenVerifier:
    entries = [issuer.config_entry(_AUDIENCE, grant_rules=_RULES) for issuer in issuers]
    return ExternalTokenVerifier(ExternalTokenConfig.model_validate({"issuers": entries}))


async def _refused(verifier: ExternalTokenVerifier, token: str, reason: Reason) -> None:
    with pytest.raises(ExternalTokenError) as caught:
        await verifier.verify(token)
    assert caught.value.reason is reason
    signature = token.rsplit(".", 1)[-1]
    assert token not in str(caught.value)
    assert not signature or signature not in str(caught.value)


async def test_allowlisted_issuer_with_the_right_audience_gives_a_principal(
    idp_a: LocalIssuer, idp_b: LocalIssuer
) -> None:
    claims = idp_b.claims(_AUDIENCE, subject="user-42", scope="files:read admin")
    principal = await _verifier(idp_a, idp_b).verify(idp_b.mint(claims))

    assert principal.key == ("https://idp-b.example", "user-42")
    assert principal.client_id == "test-client"
    assert principal.scopes == ("admin", "files:read")
    assert principal.expires_at == datetime.fromtimestamp(claims["exp"], tz=timezone.utc)
    assert [(g.capability, g.status) for g in principal.grants] == [("file.read", "active")]


async def test_parser_header_type_error_is_a_typed_refusal(
    idp_a: LocalIssuer, monkeypatch: pytest.MonkeyPatch
) -> None:
    parsed = Mock()
    parsed.headers.return_value = []
    parsed.payload = b"{}"
    monkeypatch.setattr("asap.auth.external_verifier.extract_compact", Mock(return_value=parsed))

    await _refused(_verifier(idp_a), "local.test.signature", Reason.MALFORMED_TOKEN)


async def test_same_token_used_twice_succeeds_both_times(idp_a: LocalIssuer) -> None:
    verifier, token = _verifier(idp_a), idp_a.mint(idp_a.claims(_AUDIENCE))
    assert await verifier.verify(token) == await verifier.verify(token)


def _untrusted(idp: LocalIssuer) -> str:
    evil = LocalIssuer("https://evil.example")
    return evil.mint(evil.claims(_AUDIENCE))


def _hs256(idp: LocalIssuer) -> str:
    secret = jwk.OctKey.import_key(b"hmac-secret-that-is-32-bytes-long")
    return idp.mint(idp.claims(_AUDIENCE), header={"alg": "HS256"}, key=secret)


_NOW = int(time.time())
_REFUSALS: dict[str, tuple[TokenFactory, Reason]] = {
    "not a JWT": (lambda idp: "opaque-token-value", Reason.MALFORMED_TOKEN),
    "unknown issuer": (_untrusted, Reason.UNTRUSTED_ISSUER),
    "wrong audience": (
        lambda idp: idp.mint(idp.claims("https://other.example")),
        Reason.AUDIENCE_MISMATCH,
    ),
    "expired": (
        lambda idp: idp.mint(idp.claims(_AUDIENCE, lifetime_seconds=-120)),
        Reason.TOKEN_EXPIRED,
    ),
    "not yet valid": (
        lambda idp: idp.mint(idp.claims(_AUDIENCE, nbf=_NOW + 3600)),
        Reason.TOKEN_NOT_YET_VALID,
    ),
    "agent+jwt": (
        lambda idp: idp.mint(idp.claims(_AUDIENCE), header={"typ": "agent+jwt"}),
        Reason.TOKEN_TYPE_REJECTED,
    ),
    "host+jwt": (
        lambda idp: idp.mint(idp.claims(_AUDIENCE), header={"typ": "host+jwt"}),
        Reason.TOKEN_TYPE_REJECTED,
    ),
    "application/Agent+JWT": (
        lambda idp: idp.mint(idp.claims(_AUDIENCE), header={"typ": "application/Agent+JWT"}),
        Reason.TOKEN_TYPE_REJECTED,
    ),
    "alg none": (
        lambda idp: unsigned_token({"alg": "none"}, idp.claims(_AUDIENCE)),
        Reason.ALGORITHM_REJECTED,
    ),
    "HS256": (_hs256, Reason.ALGORITHM_REJECTED),
}


@pytest.mark.parametrize("name", list(_REFUSALS))
async def test_refused_token_raises_a_typed_error_without_the_token(
    idp_a: LocalIssuer, name: str
) -> None:
    factory, reason = _REFUSALS[name]
    await _refused(_verifier(idp_a), factory(idp_a), reason)


@pytest.mark.parametrize("name", ["unknown issuer", "agent+jwt", "alg none", "HS256"])
async def test_header_and_issuer_checks_run_before_any_key_fetch(
    idp_a: LocalIssuer, name: str
) -> None:
    requests: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500)

    source = {"kind": "jwks_uri", "jwks_uri": f"{idp_a.issuer}/jwks"}
    config = ExternalTokenConfig.model_validate(
        {"issuers": [idp_a.config_entry(_AUDIENCE, key_source=source)]}
    )
    verifier = ExternalTokenVerifier(config, transport=httpx.MockTransport(record))
    factory, reason = _REFUSALS[name]
    await _refused(verifier, factory(idp_a), reason)
    assert requests == []


@pytest.mark.parametrize("spoof_kid", [False, True])
async def test_token_signed_by_issuer_a_that_claims_issuer_b_fails(
    idp_a: LocalIssuer, idp_b: LocalIssuer, spoof_kid: bool
) -> None:
    header = {"alg": idp_a.algorithm, "kid": idp_b.kid if spoof_kid else idp_a.kid}
    token = idp_b.mint(idp_b.claims(_AUDIENCE), header=header, key=idp_a.signing_key)
    await _refused(_verifier(idp_a, idp_b), token, Reason.INVALID_SIGNATURE)


@pytest.mark.parametrize("header_name", ["jwk", "jku"])
async def test_token_never_picks_its_own_key(idp_a: LocalIssuer, header_name: str) -> None:
    attacker = LocalIssuer(idp_a.issuer)
    named = attacker.jwks["keys"][0] if header_name == "jwk" else "https://evil.example/jwks"
    token = attacker.mint(attacker.claims(_AUDIENCE), header={header_name: named})
    await _refused(_verifier(idp_a), token, Reason.INVALID_SIGNATURE)
