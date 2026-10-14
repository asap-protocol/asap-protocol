"""Lock the Host JWT ``iat`` clock-skew fence at 60 seconds."""

from __future__ import annotations

import base64
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from joserfc import jwt as jose_jwt
from joserfc.jwk import OKPKey

from asap.auth.agent_jwt import (
    HOST_JWT_TYP,
    HOST_PUBLIC_KEY_CLAIM,
    JWT_ALGS_SIGN,
    verify_host_jwt,
)
from asap.auth.identity import InMemoryHostStore, jwk_thumbprint_sha256

_NOW = 2_000_000_000.0
_SKEW_SECONDS = 60


def _b64url(data: bytes) -> str:
    """Encode bytes as unpadded base64url."""
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _signing_key() -> tuple[OKPKey, dict[str, Any]]:
    """Return a fresh Ed25519 signer and its public JWK claim."""
    private_key = Ed25519PrivateKey.generate()
    raw_private = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    raw_public = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    okp = OKPKey.import_key(
        {
            "kty": "OKP",
            "crv": "Ed25519",
            "d": _b64url(raw_private),
            "x": _b64url(raw_public),
        }
    )
    return okp, dict(okp.as_dict(private=False))


def _host_token(okp: OKPKey, host_pub: dict[str, Any], *, iat: int, jti: str) -> str:
    """Sign a host JWT whose ``iat`` is the only clock input under test."""
    return jose_jwt.encode(
        {"alg": JWT_ALGS_SIGN, "typ": HOST_JWT_TYP},
        {
            "iss": jwk_thumbprint_sha256(host_pub),
            "aud": "urn:asap:agent:receiver",
            "iat": iat,
            "exp": int(_NOW) + 400,
            "jti": jti,
            HOST_PUBLIC_KEY_CLAIM: host_pub,
        },
        okp,
        algorithms=[JWT_ALGS_SIGN],
    )


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
async def test_host_jwt_accepts_iat_at_skew_and_rejects_one_second_later(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``iat`` equal to now+60s verifies; now+61s is too far in the future.

    Both tokens share ``_validate_standard_claims``, which agent JWT verification
    uses as well. No host row is stored, so this does not treat a pending host
    as active.
    """
    monkeypatch.setattr("asap.auth.agent_jwt.time.time", lambda: _NOW)
    okp, host_pub = _signing_key()
    store = InMemoryHostStore()
    now = int(_NOW)

    accepted = await verify_host_jwt(
        _host_token(okp, host_pub, iat=now + _SKEW_SECONDS, jti="j-iat-edge"),
        store,
    )
    assert accepted.ok is True
    assert accepted.host is None

    rejected = await verify_host_jwt(
        _host_token(okp, host_pub, iat=now + _SKEW_SECONDS + 1, jti="j-iat-late"),
        store,
    )
    assert rejected.ok is False
    assert rejected.error == "invalid iat (too far in the future)"
