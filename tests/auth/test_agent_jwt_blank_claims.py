"""Lock blank subject, issuer, and jti rejection on agent JWT verify.

Omitted ``sub`` and empty ``iss`` / ``jti`` are already covered. Whitespace is
truthy, and a JSON number is not a missing key, so those inputs only fail
closed because of ``isinstance`` and ``str.strip``.
"""

from __future__ import annotations

import base64
import json
import time
from datetime import datetime, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from joserfc import jwt as jose_jwt
from joserfc.jwk import OKPKey

from asap.auth.agent_jwt import AGENT_JWT_TYP, JWT_ALGS_SIGN, verify_agent_jwt
from asap.auth.identity import (
    AgentSession,
    HostIdentity,
    InMemoryAgentStore,
    InMemoryHostStore,
    jwk_thumbprint_sha256,
)
from tests.crypto.jwk_helpers import ed25519_public_jwk

_AGENT_ID = "a1"
_HOST_ID = "h1"


def _b64url(data: bytes) -> str:
    """Encode JWT segments without base64 padding.

    joserfc's compact parser accepts the unpadded form issuers send on the wire.
    """
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unsigned_agent_token(payload: dict[str, object]) -> str:
    """Build a compact token whose payload is parsed before the signature."""
    header = _b64url(json.dumps({"alg": "EdDSA", "typ": AGENT_JWT_TYP}).encode("utf-8"))
    body = _b64url(json.dumps(payload).encode("utf-8"))
    return f"{header}.{body}.{_b64url(b'not-a-signature')}"


def _signing_okp(private_key: Ed25519PrivateKey) -> OKPKey:
    """Build an OKP JWK that includes the private ``d`` parameter."""
    raw_private = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    raw_public = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return OKPKey.import_key(
        {
            "kty": "OKP",
            "crv": "Ed25519",
            "d": _b64url(raw_private),
            "x": _b64url(raw_public),
        }
    )


class _RecordingAgentStore(InMemoryAgentStore):
    """Counts ``get`` calls so a blank subject cannot be used as an id."""

    def __init__(self) -> None:
        super().__init__()
        self.lookups: list[str] = []

    async def get(self, agent_id: str) -> AgentSession | None:
        self.lookups.append(agent_id)
        return await super().get(agent_id)


class _RecordingHostStore(InMemoryHostStore):
    """Counts thumbprint lookups so a blank issuer is not used as a key."""

    def __init__(self) -> None:
        super().__init__()
        self.thumb_lookups: list[str] = []

    async def get_by_public_key(self, thumbprint: str) -> HostIdentity | None:
        self.thumb_lookups.append(thumbprint)
        return await super().get_by_public_key(thumbprint)


async def _seed_active_agent() -> tuple[_RecordingHostStore, InMemoryAgentStore, OKPKey, str]:
    """Return a live host/agent pair, the agent signing key, and the host thumbprint."""
    now = datetime.now(timezone.utc)
    host_key = Ed25519PrivateKey.generate()
    agent_key = Ed25519PrivateKey.generate()
    host_public = ed25519_public_jwk(host_key)
    hosts = _RecordingHostStore()
    agents = InMemoryAgentStore()
    await hosts.save(
        HostIdentity(
            host_id=_HOST_ID,
            public_key=host_public,
            status="active",
            created_at=now,
            updated_at=now,
        )
    )
    await agents.save(
        AgentSession(
            agent_id=_AGENT_ID,
            host_id=_HOST_ID,
            public_key=ed25519_public_jwk(agent_key),
            mode="delegated",
            status="active",
            created_at=now,
        )
    )
    return hosts, agents, _signing_okp(agent_key), jwk_thumbprint_sha256(host_public)


def _encode_agent(signing_key: OKPKey, *, issuer: str, jti: str) -> str:
    """Sign an agent JWT with the supplied issuer and jti."""
    issued_at = int(time.time())
    return jose_jwt.encode(
        {"alg": JWT_ALGS_SIGN, "typ": AGENT_JWT_TYP},
        {
            "iss": issuer,
            "sub": _AGENT_ID,
            "aud": "a",
            "iat": issued_at,
            "exp": issued_at + 60,
            "jti": jti,
        },
        signing_key,
        algorithms=[JWT_ALGS_SIGN],
    )


@pytest.mark.parametrize("subject", ["   ", "\t\n", "", 42])
async def test_verify_agent_jwt_blank_or_non_string_sub_skips_store(subject: object) -> None:
    """Whitespace and non-string subjects are missing ids, not lookup keys."""
    agents = _RecordingAgentStore()
    token = _unsigned_agent_token({"sub": subject, "iss": "host-tp"})

    result = await verify_agent_jwt(token, InMemoryHostStore(), agents)

    assert result.ok is False
    assert result.error == "missing sub (agent id)"
    assert agents.lookups == []


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
@pytest.mark.parametrize("issuer", ["   ", "\t\n"])
async def test_verify_agent_jwt_whitespace_iss_skips_host_lookup(issuer: str) -> None:
    """A whitespace issuer is rejected before the host thumbprint index is read."""
    hosts, agents, signing_key, _thumbprint = await _seed_active_agent()
    token = _encode_agent(signing_key, issuer=issuer, jti="j-blank-iss")

    result = await verify_agent_jwt(token, hosts, agents)

    assert result.ok is False
    assert result.error == "missing iss (host thumbprint)"
    assert hosts.thumb_lookups == []


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
@pytest.mark.parametrize("jti", ["   ", "\t"])
async def test_verify_agent_jwt_whitespace_jti_is_missing(jti: str) -> None:
    """A whitespace jti is missing, so verification stops before the host lookup."""
    hosts, agents, signing_key, thumbprint = await _seed_active_agent()
    token = _encode_agent(signing_key, issuer=thumbprint, jti=jti)

    result = await verify_agent_jwt(token, hosts, agents)

    assert result.ok is False
    assert result.error == "missing jti"
    assert hosts.thumb_lookups == []
