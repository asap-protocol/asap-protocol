"""Lock RFC 9864 ``alg: Ed25519`` on host and agent JWT verification.

Issuers still sign ``alg: EdDSA``. ``JWT_ALGS_VERIFY`` also lists ``Ed25519``,
and both ``verify_host_jwt`` and ``verify_agent_jwt`` pass that list to
joserfc. These tests sign through the real creators with the alias forced,
then require each verifier to accept it.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from asap.auth.agent_jwt import create_agent_jwt, create_host_jwt, verify_agent_jwt, verify_host_jwt
from asap.auth.identity import (
    AgentSession,
    HostIdentity,
    InMemoryAgentStore,
    InMemoryHostStore,
    jwk_thumbprint_sha256,
)
from tests.crypto.jwk_helpers import ed25519_public_jwk

_ED25519_ALG = "Ed25519"
_AUDIENCE = "alias-aud"


def _jose_alg(token: str) -> str:
    """Return the JOSE ``alg`` so a no-op sign patch cannot pass verification."""
    header_segment = token.split(".", maxsplit=1)[0]
    padding = "=" * (-len(header_segment) % 4)
    header = json.loads(base64.urlsafe_b64decode(header_segment + padding))
    alg = header.get("alg")
    if not isinstance(alg, str):
        raise AssertionError(f"JWT alg must be a string, got {alg!r}")
    return alg


def _sign_with_alg(monkeypatch: pytest.MonkeyPatch, alg: str) -> None:
    """Point both JWT creators at ``alg`` for this test."""
    monkeypatch.setattr("asap.auth.agent_jwt.JWT_ALGS_SIGN", alg)


def _flip_signature(token: str) -> str:
    """Flip the first signature character. The last character is only padding."""
    header, payload, signature = token.split(".")
    if len(signature) < 2:
        raise AssertionError(f"JWT signature segment is too short to flip: {signature!r}")
    flipped = "A" if signature[0] != "A" else "B"
    return f"{header}.{payload}.{flipped}{signature[1:]}"


async def _active_agent_context() -> tuple[
    InMemoryHostStore, InMemoryAgentStore, str, Ed25519PrivateKey
]:
    """Register one active host and the agent key that will sign the alias token."""
    now = datetime.now(timezone.utc)
    host_key = Ed25519PrivateKey.generate()
    host_public = ed25519_public_jwk(host_key)
    agent_key = Ed25519PrivateKey.generate()
    hosts = InMemoryHostStore()
    agents = InMemoryAgentStore()
    await hosts.save(
        HostIdentity(
            host_id="host-ed25519",
            public_key=host_public,
            status="active",
            created_at=now,
            updated_at=now,
        )
    )
    await agents.save(
        AgentSession(
            agent_id="agent-ed25519",
            host_id="host-ed25519",
            public_key=ed25519_public_jwk(agent_key),
            mode="delegated",
            status="active",
            created_at=now,
        )
    )
    return hosts, agents, jwk_thumbprint_sha256(host_public), agent_key


async def test_verify_host_jwt_accepts_ed25519_alg(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dynamic host registration accepts a token whose header alg is Ed25519."""
    _sign_with_alg(monkeypatch, _ED25519_ALG)
    host_key = Ed25519PrivateKey.generate()
    token = create_host_jwt(host_key, aud=_AUDIENCE)
    assert _jose_alg(token) == _ED25519_ALG

    result = await verify_host_jwt(token, InMemoryHostStore())

    assert result.ok is True
    assert result.error is None
    assert result.host is None
    assert result.claims is not None
    assert result.claims["aud"] == _AUDIENCE


async def test_verify_agent_jwt_accepts_ed25519_alg(monkeypatch: pytest.MonkeyPatch) -> None:
    """Agent verification accepts the same alias on its own decode site."""
    _sign_with_alg(monkeypatch, _ED25519_ALG)
    hosts, agents, host_thumbprint, agent_key = await _active_agent_context()
    token = create_agent_jwt(
        agent_key,
        host_thumbprint=host_thumbprint,
        agent_id="agent-ed25519",
        aud=_AUDIENCE,
    )
    assert _jose_alg(token) == _ED25519_ALG

    result = await verify_agent_jwt(token, hosts, agents)

    assert result.ok is True
    assert result.error is None
    assert result.agent is not None
    assert result.agent.agent_id == "agent-ed25519"
    assert result.claims is not None
    assert result.claims["aud"] == _AUDIENCE


_NONE_ALG_WARNING = "ignore:JWS algorithm .none. is deprecated:joserfc.errors.SecurityWarning"


async def test_verify_host_jwt_rejects_tampered_ed25519_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Returning the unverified payload must not accept a broken Ed25519 signature."""
    _sign_with_alg(monkeypatch, _ED25519_ALG)
    token = create_host_jwt(Ed25519PrivateKey.generate(), aud=_AUDIENCE)

    result = await verify_host_jwt(_flip_signature(token), InMemoryHostStore())

    assert result.ok is False
    assert result.claims is None
    assert result.error is not None
    assert result.error.startswith("invalid host JWT")


@pytest.mark.filterwarnings(_NONE_ALG_WARNING)
async def test_verify_host_jwt_rejects_alg_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unsigned host token stays invalid while ``none`` is off the verify list."""
    _sign_with_alg(monkeypatch, "none")
    token = create_host_jwt(Ed25519PrivateKey.generate(), aud=_AUDIENCE)
    assert _jose_alg(token) == "none"

    result = await verify_host_jwt(token, InMemoryHostStore())

    assert result.ok is False
    assert result.claims is None


async def test_verify_agent_jwt_rejects_tampered_ed25519_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The agent decode site must check the Ed25519 signature, not only the header."""
    _sign_with_alg(monkeypatch, _ED25519_ALG)
    hosts, agents, host_thumbprint, agent_key = await _active_agent_context()
    token = create_agent_jwt(
        agent_key,
        host_thumbprint=host_thumbprint,
        agent_id="agent-ed25519",
        aud=_AUDIENCE,
    )

    result = await verify_agent_jwt(_flip_signature(token), hosts, agents)

    assert result.ok is False
    assert result.claims is None
    assert result.error is not None
    assert result.error.startswith("invalid agent JWT")


@pytest.mark.filterwarnings(_NONE_ALG_WARNING)
async def test_verify_agent_jwt_rejects_alg_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unsigned agent token stays invalid while ``none`` is off the verify list."""
    _sign_with_alg(monkeypatch, "none")
    hosts, agents, host_thumbprint, agent_key = await _active_agent_context()
    token = create_agent_jwt(
        agent_key,
        host_thumbprint=host_thumbprint,
        agent_id="agent-ed25519",
        aud=_AUDIENCE,
    )
    assert _jose_alg(token) == "none"

    result = await verify_agent_jwt(token, hosts, agents)

    assert result.ok is False
    assert result.claims is None
