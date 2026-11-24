"""The local test issuer: keys, static JWKS, and token minting."""

from __future__ import annotations

import base64
import json

import pytest
from joserfc import jwk
from joserfc import jwt as jose_jwt

from asap.auth.external_tokens import ExternalIssuerConfig
from tests.auth.local_issuer import LocalIssuer, LocalIssuerAlgorithm, unsigned_token

_AUDIENCE = "https://api.example/asap"


def _segment(token: str, index: int) -> dict[str, object]:
    part = token.split(".")[index]
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


@pytest.mark.parametrize("algorithm", ["ES256", "RS256", "EdDSA"])
def test_minted_token_verifies_with_the_static_jwks(algorithm: LocalIssuerAlgorithm) -> None:
    issuer = LocalIssuer(algorithm=algorithm)
    token = issuer.mint(issuer.claims(_AUDIENCE, scope="files:read"))

    keys = jwk.KeySet.import_key_set(issuer.jwks)
    decoded = jose_jwt.decode(token, keys, algorithms=[algorithm])

    assert decoded.header == {"alg": algorithm, "kid": issuer.kid, "typ": "at+jwt"}
    assert decoded.claims["iss"] == issuer.issuer
    assert decoded.claims["aud"] == _AUDIENCE
    assert decoded.claims["scope"] == "files:read"


def test_jwks_holds_only_the_public_key() -> None:
    issuer = LocalIssuer(algorithm="RS256")
    keys = jwk.KeySet.import_key_set(issuer.jwks).keys
    assert [key.kid for key in keys] == [issuer.kid]
    assert not any(key.is_private for key in keys)


def test_config_entry_is_a_static_issuer_with_its_rules() -> None:
    issuer = LocalIssuer("https://idp.example")
    rule = {"scope": "files:read", "capability": "file.read"}
    entry = ExternalIssuerConfig.model_validate(issuer.config_entry(_AUDIENCE, grant_rules=[rule]))
    assert entry.issuer == "https://idp.example"
    assert entry.key_source is not None and entry.key_source.kind == "static_jwks"
    assert [r.capability for r in entry.grant_rules] == ["file.read"]


def test_claims_have_defaults_and_take_overrides() -> None:
    issuer = LocalIssuer()
    claims = issuer.claims(_AUDIENCE, subject="user-9", lifetime_seconds=60, nbf=5)
    assert claims["iss"] == issuer.issuer and claims["sub"] == "user-9"
    assert claims["exp"] - claims["iat"] == 60
    assert claims["nbf"] == 5
    assert "scope" not in claims


def test_header_overrides_change_or_remove_fields() -> None:
    issuer = LocalIssuer()
    token = issuer.mint(issuer.claims(_AUDIENCE), header={"typ": "agent+jwt", "kid": None})
    assert _segment(token, 0) == {"alg": "ES256", "typ": "agent+jwt"}


def test_mint_signs_with_another_key_but_keeps_this_kid() -> None:
    issuer, other = LocalIssuer(), LocalIssuer()
    token = issuer.mint(issuer.claims(_AUDIENCE), key=other.signing_key)
    assert _segment(token, 0)["kid"] == issuer.kid
    jose_jwt.decode(token, other.signing_key, algorithms=["ES256"])


def test_unsigned_token_has_alg_none_and_an_empty_signature() -> None:
    token = unsigned_token({"alg": "none", "typ": "at+jwt"}, {"iss": "https://issuer.example"})
    assert token.endswith(".")
    assert _segment(token, 0) == {"alg": "none", "typ": "at+jwt"}
    assert _segment(token, 1) == {"iss": "https://issuer.example"}


def test_rotate_key_replaces_the_key_and_its_kid() -> None:
    issuer = LocalIssuer()
    old_kid = issuer.kid
    issuer.rotate_key()
    assert issuer.kid != old_kid
    assert [key["kid"] for key in issuer.jwks["keys"]] == [issuer.kid]
