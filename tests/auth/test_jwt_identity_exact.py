"""Lock Host/Agent JWT ``typ`` and agent-id lookup to exact strings.

A case-folded ``typ`` or a prefix lookup on ``sub`` would authorize a different
token or a different agent. These tests fail when that comparison widens.
"""

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
    HOST_PUBLIC_KEY_CLAIM,
    JWT_ALGS_SIGN,
    create_agent_jwt,
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

_AGENT_ID = "bot-01"
_AGENT_NEIGHBORS = (
    "bot-010",
    "bot-0",
    "Bot-01",
    " bot-01",
    "bot-01 ",
)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _public_jwk(private_key: Ed25519PrivateKey) -> dict[str, Any]:
    raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return {"kty": "OKP", "crv": "Ed25519", "x": _b64url(raw)}


def _signing_okp(private_key: Ed25519PrivateKey) -> OKPKey:
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


def _sign_host_with_typ(host_key: Ed25519PrivateKey, typ: str) -> str:
    """Sign a Host JWT whose only intended defect is ``typ``."""
    okp = _signing_okp(host_key)
    host_pub = dict(okp.as_dict(private=False))
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": jwk_thumbprint_sha256(host_pub),
        "aud": "urn:asap:agent:bot-01",
        "iat": now,
        "exp": now + 120,
        "jti": "jwt_host_typ",
        HOST_PUBLIC_KEY_CLAIM: host_pub,
    }
    header = {"alg": JWT_ALGS_SIGN, "typ": typ}
    return jose_jwt.encode(header, claims, okp, algorithms=[JWT_ALGS_SIGN])


def _sign_agent_with_typ(
    agent_key: Ed25519PrivateKey,
    host_thumbprint: str,
    agent_id: str,
    typ: str,
) -> str:
    """Sign an Agent JWT whose only intended defect is ``typ``."""
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": host_thumbprint,
        "sub": agent_id,
        "aud": "aud",
        "iat": now,
        "exp": now + 60,
        "jti": "jwt_agent_typ",
    }
    header = {"alg": JWT_ALGS_SIGN, "typ": typ}
    return jose_jwt.encode(header, claims, _signing_okp(agent_key), algorithms=[JWT_ALGS_SIGN])


async def _save_active_agent(
    agent_id: str,
) -> tuple[InMemoryHostStore, InMemoryAgentStore, str, Ed25519PrivateKey]:
    now = datetime.now(timezone.utc)
    host_key = Ed25519PrivateKey.generate()
    host_pub = _public_jwk(host_key)
    agent_key = Ed25519PrivateKey.generate()
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
            agent_id=agent_id,
            host_id="h1",
            public_key=_public_jwk(agent_key),
            mode="delegated",
            status="active",
            created_at=now,
        )
    )
    return hosts, agents, jwk_thumbprint_sha256(host_pub), agent_key


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
@pytest.mark.parametrize("typ", ["HOST+JWT", "Host+jwt", " host+jwt", "host+jwt ", "host", "jwt"])
async def test_host_jwt_typ_match_is_exact(typ: str) -> None:
    """``typ`` must be ``host+jwt``. Case, spaces, and substrings stay invalid."""
    token = _sign_host_with_typ(Ed25519PrivateKey.generate(), typ)
    result = await verify_host_jwt(token, InMemoryHostStore())
    assert result.ok is False
    assert result.error == "invalid typ for host JWT"


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
@pytest.mark.parametrize(
    "typ", ["AGENT+JWT", "Agent+jwt", " agent+jwt", "agent+jwt ", "agent", "jwt"]
)
async def test_agent_jwt_typ_match_is_exact(typ: str) -> None:
    """``typ`` must be ``agent+jwt``. A short token must not verify a real session."""
    hosts, agents, host_tp, agent_key = await _save_active_agent(_AGENT_ID)
    token = _sign_agent_with_typ(agent_key, host_tp, _AGENT_ID, typ)
    result = await verify_agent_jwt(token, hosts, agents)
    assert result.ok is False
    assert result.error == "invalid typ for agent JWT"


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
async def test_agent_jwt_subject_lookup_is_exact() -> None:
    """Store lookup uses the raw ``sub``. Neighbors are unknown agents."""
    hosts, agents, host_tp, agent_key = await _save_active_agent(_AGENT_ID)
    exact = create_agent_jwt(agent_key, host_thumbprint=host_tp, agent_id=_AGENT_ID, aud="aud")
    exact_result = await verify_agent_jwt(exact, hosts, agents)
    assert exact_result.ok is True
    assert exact_result.agent is not None
    assert exact_result.agent.agent_id == _AGENT_ID

    for subject in _AGENT_NEIGHBORS:
        token = create_agent_jwt(agent_key, host_thumbprint=host_tp, agent_id=subject, aud="aud")
        result = await verify_agent_jwt(token, hosts, agents)
        assert result.ok is False
        assert result.error == "unknown agent"
        assert result.agent is None
