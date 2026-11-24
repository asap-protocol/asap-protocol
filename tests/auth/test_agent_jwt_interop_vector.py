"""Cross-language Agent JWT vector (v2.6.0).

``packages/typescript/client/test/agent-jwt-verify.test.ts`` checks the token in
``tests/fixtures/interop/agent-jwt.json`` with ``verifyAgentJwt``. This module
proves that the token is what ``create_agent_jwt`` mints from the fixed keys,
clock, and ``jti``, and that ``verify_agent_jwt`` accepts it at the same clock.
Without this test the vector would only be a copy.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from asap.auth.agent_jwt import JtiReplayCache, create_agent_jwt, verify_agent_jwt
from asap.auth.identity import (
    AgentSession,
    HostIdentity,
    InMemoryAgentStore,
    InMemoryHostStore,
    jwk_thumbprint_sha256,
)
from tests.crypto.jwk_helpers import ed25519_public_jwk

VECTOR_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "interop" / "agent-jwt.json"
VECTOR: dict[str, Any] = json.loads(VECTOR_PATH.read_text(encoding="utf-8"))
JTI_PREFIX = "jwt_"


def _test_key(label: str) -> Ed25519PrivateKey:
    """Derive a test-only Ed25519 key from a fixed label, so the vector is reproducible."""
    return Ed25519PrivateKey.from_private_bytes(hashlib.sha256(label.encode("utf-8")).digest())


AGENT_KEY = _test_key("asap interop vector: agent key")
HOST_KEY = _test_key("asap interop vector: host key")


def _freeze_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    fixed_now = float(VECTOR["now"])
    monkeypatch.setattr("time.time", lambda: fixed_now)


def test_vector_keys_are_the_fixed_test_keys() -> None:
    """The public key and host thumbprint in the vector come from the labels above."""
    assert VECTOR["agent_public_jwk"] == ed25519_public_jwk(AGENT_KEY)
    assert VECTOR["host_thumbprint"] == jwk_thumbprint_sha256(ed25519_public_jwk(HOST_KEY))


def test_create_agent_jwt_mints_the_vector_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ed25519 signatures are deterministic, so the same inputs give the same token."""
    _freeze_clock(monkeypatch)
    jti_suffix = VECTOR["jti"].removeprefix(JTI_PREFIX)
    monkeypatch.setattr("asap.auth.agent_jwt.generate_id", lambda: jti_suffix)

    token = create_agent_jwt(
        AGENT_KEY,
        host_thumbprint=VECTOR["host_thumbprint"],
        agent_id=VECTOR["agent_id"],
        aud=VECTOR["audience"],
        capabilities=VECTOR["capabilities"],
    )

    assert token == VECTOR["token"]


async def test_verify_agent_jwt_accepts_the_vector_at_the_fixed_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Python verifier accepts the vector, as the TypeScript verifier must."""
    created_at = datetime.now(timezone.utc)
    hosts = InMemoryHostStore()
    agents = InMemoryAgentStore()
    await hosts.save(
        HostIdentity(
            host_id="host-interop-vector",
            public_key=ed25519_public_jwk(HOST_KEY),
            status="active",
            created_at=created_at,
            updated_at=created_at,
        )
    )
    await agents.save(
        AgentSession(
            agent_id=VECTOR["agent_id"],
            host_id="host-interop-vector",
            public_key=VECTOR["agent_public_jwk"],
            mode="delegated",
            status="active",
            created_at=created_at,
        )
    )
    _freeze_clock(monkeypatch)

    result = await verify_agent_jwt(
        VECTOR["token"],
        hosts,
        agents,
        expected_audience=VECTOR["audience"],
        jti_replay_cache=JtiReplayCache(),
    )

    assert result.ok, result.error
    assert result.claims is not None
    assert result.claims["jti"] == VECTOR["jti"]
    assert result.capabilities == VECTOR["capabilities"]
