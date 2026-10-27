"""Lock audience binding when a verifier requires an audience."""

from __future__ import annotations

import base64
import time
from datetime import datetime, timezone
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from joserfc import jwt as jose_jwt
from joserfc.jwk import OKPKey

from asap.auth.agent_jwt import (
    AGENT_JWT_TYP,
    HOST_JWT_TYP,
    HOST_PUBLIC_KEY_CLAIM,
    JWT_ALGS_SIGN,
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

_EXPECTED_AUDIENCE = "urn:asap:agent:identity"


def _b64url(data: bytes) -> str:
    """Encode bytes as unpadded base64url."""
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _signing_key() -> tuple[OKPKey, dict[str, Any]]:
    """Return a fresh Ed25519 signer and its public JWK."""
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


def _encode(typ: str, claims: dict[str, Any], okp: OKPKey) -> str:
    """Sign a compact JWT with the given ``typ`` header."""
    return jose_jwt.encode(
        {"alg": JWT_ALGS_SIGN, "typ": typ},
        claims,
        okp,
        algorithms=[JWT_ALGS_SIGN],
    )


def _standard_claims(*, iss: str, jti: str) -> dict[str, Any]:
    """Build clock claims that pass expiry and future-iat checks."""
    now = int(time.time())
    return {"iss": iss, "iat": now, "exp": now + 300, "jti": jti}


async def _save_active_agent() -> tuple[InMemoryHostStore, InMemoryAgentStore, str, OKPKey]:
    """Persist an active host and agent, and return the agent signing key."""
    now = datetime.now(timezone.utc)
    _host_okp, host_pub = _signing_key()
    agent_okp, agent_pub = _signing_key()
    hosts = InMemoryHostStore()
    agents = InMemoryAgentStore()
    await hosts.save(
        HostIdentity(
            host_id="h1",
            public_key=host_pub,
            status="active",
            created_at=now,
            updated_at=now,
        )
    )
    await agents.save(
        AgentSession(
            agent_id="a1",
            host_id="h1",
            public_key=agent_pub,
            mode="delegated",
            status="active",
            created_at=now,
        )
    )
    return hosts, agents, jwk_thumbprint_sha256(host_pub), agent_okp


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
@pytest.mark.parametrize("aud", [None, []], ids=["missing", "empty-list"])
async def test_host_jwt_rejects_unmatched_aud_only_when_audience_required(
    aud: list[str] | None,
) -> None:
    """Missing ``aud`` and ``aud: []`` verify until an audience is required.

    When ``expected_audience`` is set, an absent or empty audience must not
    count as a match. Otherwise a token can be replayed onto every service
    that only checks for a non-empty ``aud`` claim.
    """
    okp, host_pub = _signing_key()
    claims = _standard_claims(iss=jwk_thumbprint_sha256(host_pub), jti=f"j-host-{aud!s}")
    claims[HOST_PUBLIC_KEY_CLAIM] = host_pub
    if aud is not None:
        claims["aud"] = aud
    token = _encode(HOST_JWT_TYP, claims, okp)
    store = InMemoryHostStore()

    open_result = await verify_host_jwt(token, store)
    assert open_result.ok is True
    assert open_result.host is None

    bound = await verify_host_jwt(token, store, expected_audience=_EXPECTED_AUDIENCE)
    assert bound.ok is False
    assert bound.error == "audience mismatch"


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
async def test_agent_jwt_rejects_missing_aud_only_when_audience_required() -> None:
    """Agent verification forwards ``expected_audience`` into the shared claim gate."""
    hosts, agents, host_tp, agent_okp = await _save_active_agent()
    claims = _standard_claims(iss=host_tp, jti="j-agent-no-aud")
    claims["sub"] = "a1"
    token = _encode(AGENT_JWT_TYP, claims, agent_okp)

    open_result = await verify_agent_jwt(token, hosts, agents)
    assert open_result.ok is True

    bound = await verify_agent_jwt(token, hosts, agents, expected_audience=_EXPECTED_AUDIENCE)
    assert bound.ok is False
    assert bound.error == "audience mismatch"
