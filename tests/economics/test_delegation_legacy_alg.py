"""Lock legacy delegation JWTs that still use ``alg: EdDSA``.

Current issuance signs ``alg: Ed25519``. Verification must keep accepting
``EdDSA`` and must still check the signature. ``alg: none`` is not a
delegation algorithm.
"""

from __future__ import annotations

import base64
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from joserfc import jwt as jose_jwt

from asap.crypto.keys import generate_keypair
from asap.economics.delegation import (
    _ed25519_private_key_to_okp_key,
    validate_delegation,
)

_DELEGATOR = "urn:asap:agent:root"
_DELEGATE = "urn:asap:agent:child"
_JTI = "del_legacy_eddsa"
pytestmark = pytest.mark.filterwarnings(
    "ignore:JWS algorithm .none. is deprecated:joserfc.errors.SecurityWarning"
)


def _jose_alg(token: str) -> str:
    """Return the unprotected JOSE ``alg`` (callers still verify the signature)."""
    header_segment = token.split(".", maxsplit=1)[0]
    padding = "=" * (-len(header_segment) % 4)
    header = json.loads(base64.urlsafe_b64decode(header_segment + padding))
    alg = header.get("alg")
    if not isinstance(alg, str):
        raise AssertionError(f"JWT alg must be a string, got {alg!r}")
    return alg


def _signed_token(alg: str) -> tuple[str, Ed25519PublicKey]:
    """Sign one delegation whose header ``alg`` is exactly ``alg``."""
    private_key, public_key = generate_keypair()
    claims: dict[str, object] = {
        "iss": _DELEGATOR,
        "aud": _DELEGATE,
        "jti": _JTI,
        "iat": 1_700_000_000,
        "exp": 4_000_000_000,
        "scp": ["task.execute"],
    }
    token = jose_jwt.encode(
        {"alg": alg, "typ": "JWT"},
        claims,
        _ed25519_private_key_to_okp_key(private_key),
        algorithms=[alg],
    )
    return token, public_key


def _tamper_signature(token: str) -> str:
    """Flip the last signature character without changing the segment count."""
    header, payload, signature = token.split(".")
    replacement = "A" if signature[-1:] != "A" else "B"
    return f"{header}.{payload}.{signature[:-1]}{replacement}"


def test_validate_delegation_accepts_legacy_eddsa_header() -> None:
    """A signature under ``alg: EdDSA`` still grants ``task.execute``."""
    token, public_key = _signed_token("EdDSA")
    assert _jose_alg(token) == "EdDSA"

    result = validate_delegation(
        token,
        "task.execute",
        public_key_resolver=lambda _issuer: public_key,
    )

    assert result.success is True
    assert result.error is None
    assert result.delegator == _DELEGATOR
    assert result.delegate == _DELEGATE
    assert result.jti == _JTI
    assert result.scopes == ["task.execute"]


def test_validate_delegation_rejects_tampered_legacy_eddsa_signature() -> None:
    """Allowing the legacy alg must not skip the Ed25519 signature check."""
    token, public_key = _signed_token("EdDSA")

    result = validate_delegation(
        _tamper_signature(token),
        "task.execute",
        public_key_resolver=lambda _issuer: public_key,
    )

    assert result.success is False
    assert result.error is not None
    assert result.error.startswith("Invalid or expired token")
    assert result.delegator is None
    assert result.jti is None


def test_validate_delegation_rejects_alg_none() -> None:
    """An unsigned ``alg: none`` token is not a valid delegation."""
    token, public_key = _signed_token("none")
    assert _jose_alg(token) == "none"

    result = validate_delegation(
        token,
        "task.execute",
        public_key_resolver=lambda _issuer: public_key,
    )

    assert result.success is False
    assert result.error is not None
    assert "none" in result.error
    assert result.delegator is None
    assert result.jti is None
