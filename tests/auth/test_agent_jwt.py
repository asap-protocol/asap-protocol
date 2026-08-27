"""Tests for Host JWT and Agent JWT builders and verification (S2.1 / S2.2)."""

from __future__ import annotations

import base64
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from joserfc import jwt as jose_jwt
from joserfc.jwk import OKPKey

from asap.auth.agent_jwt import (
    AGENT_JWT_TTL_SECONDS,
    AGENT_JWT_TYP,
    CAPABILITIES_CLAIM,
    HOST_JWT_TYP,
    HOST_PUBLIC_KEY_CLAIM,
    JWT_ALGS_SIGN,
    JWT_ALGS_VERIFY,
    JtiReplayCache,
    create_agent_jwt,
    create_host_jwt,
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


def _public_jwk_dict(private_key: Ed25519PrivateKey) -> dict[str, Any]:
    """Public JWK dict (OKP / Ed25519) for identity models."""
    raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    x = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return {"kty": "OKP", "crv": "Ed25519", "x": x}


def _test_b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _ed25519_to_okp_signing_key(private_key: Ed25519PrivateKey) -> OKPKey:
    """Build OKP JWK with private ``d`` for signing (matches ``agent_jwt`` helpers)."""
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
            "d": _test_b64url(raw_private),
            "x": _test_b64url(raw_public),
        }
    )


def _public_okp(private_key: Ed25519PrivateKey) -> OKPKey:
    """Public OKP JWK for verifying tokens signed by ``private_key``."""
    raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    x = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return OKPKey.import_key({"kty": "OKP", "crv": "Ed25519", "x": x})


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
def test_host_jwt_round_trip_and_claims() -> None:
    """Create host JWT, verify signature, check ``iss`` and embedded JWK claims."""
    sk = Ed25519PrivateKey.generate()
    token = create_host_jwt(sk, aud="https://asap.example", ttl_seconds=120)
    pub = _public_okp(sk)
    decoded = jose_jwt.decode(token, pub, algorithms=JWT_ALGS_VERIFY)
    assert decoded.header.get("typ") == HOST_JWT_TYP
    claims = dict(decoded.claims)
    host_pub = claims[HOST_PUBLIC_KEY_CLAIM]
    assert isinstance(host_pub, dict)
    assert claims["iss"] == jwk_thumbprint_sha256(host_pub)
    assert claims["aud"] == "https://asap.example"


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
def test_host_jwt_with_optional_agent_public_key() -> None:
    """Optional ``agent_public_key`` claim is present when provided."""
    sk = Ed25519PrivateKey.generate()
    agent_pk = {"kty": "OKP", "crv": "Ed25519", "x": "dGVzdA"}
    token = create_host_jwt(
        sk,
        aud="urn:asap:agent:test-server",
        agent_public_key=agent_pk,
    )
    pub = _public_okp(sk)
    decoded = jose_jwt.decode(token, pub, algorithms=JWT_ALGS_VERIFY)
    claims = dict(decoded.claims)
    assert claims.get("agent_public_key") == agent_pk


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
def test_agent_jwt_round_trip_and_claims() -> None:
    """Create agent JWT, verify signature, check ``iss``, ``sub``, TTL, capabilities."""
    agent_sk = Ed25519PrivateKey.generate()
    host_tp = "BeHE0RFM9jC46s0RCLfWvd-yfBVwRzIYZ_fp_IpsoUs"
    token = create_agent_jwt(
        agent_sk,
        host_thumbprint=host_tp,
        agent_id="agent-urn-1",
        aud="https://asap.example/asap",
        capabilities=["asap:read", "asap:execute"],
    )
    pub = _public_okp(agent_sk)
    decoded = jose_jwt.decode(token, pub, algorithms=JWT_ALGS_VERIFY)
    assert decoded.header.get("typ") == AGENT_JWT_TYP
    claims = dict(decoded.claims)
    assert claims["iss"] == host_tp
    assert claims["sub"] == "agent-urn-1"
    assert claims["aud"] == "https://asap.example/asap"
    assert claims[CAPABILITIES_CLAIM] == ["asap:read", "asap:execute"]
    exp = int(claims["exp"])
    iat = int(claims["iat"])
    assert exp - iat == AGENT_JWT_TTL_SECONDS


# --- Verification (S2.2) ---


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
async def test_verify_host_jwt_with_registered_host() -> None:
    """Registered host resolves; claims and host row returned."""
    now = datetime.now(timezone.utc)
    sk = Ed25519PrivateKey.generate()
    pub = _public_jwk_dict(sk)
    hosts = InMemoryHostStore()
    await hosts.save(
        HostIdentity(
            host_id="h1",
            public_key=pub,
            status="active",
            created_at=now,
            updated_at=now,
        )
    )
    token = create_host_jwt(sk, aud="https://aud.example")
    res = await verify_host_jwt(token, hosts)
    assert res.ok
    assert res.host is not None and res.host.host_id == "h1"
    assert res.claims is not None and res.claims["aud"] == "https://aud.example"


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
async def test_verify_host_jwt_audience_mismatch() -> None:
    """When ``expected_audience`` is set, wrong ``aud`` in token fails."""
    sk = Ed25519PrivateKey.generate()
    hosts = InMemoryHostStore()
    token = create_host_jwt(sk, aud="payment-service")
    res = await verify_host_jwt(
        token,
        hosts,
        expected_audience="urn:asap:agent:identity",
    )
    assert not res.ok
    assert res.error == "audience mismatch"


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
async def test_verify_host_jwt_list_aud_round_trip() -> None:
    """``aud`` as list matches when ``expected_audience`` lists overlap."""
    sk = Ed25519PrivateKey.generate()
    hosts = InMemoryHostStore()
    token = create_host_jwt(sk, aud=["aud1", "aud2"])
    res = await verify_host_jwt(token, hosts, expected_audience=["aud2", "aud3"])
    assert res.ok
    assert res.claims is not None
    assert res.claims["aud"] == ["aud1", "aud2"]


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
def test_host_jwt_round_trip_list_audience() -> None:
    sk = Ed25519PrivateKey.generate()
    token = create_host_jwt(sk, aud=["a", "b"])
    pub = _public_okp(sk)
    decoded = jose_jwt.decode(token, pub, algorithms=JWT_ALGS_VERIFY)
    claims = dict(decoded.claims)
    assert claims["aud"] == ["a", "b"]


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
async def test_verify_host_jwt_list_aud_no_overlap() -> None:
    sk = Ed25519PrivateKey.generate()
    hosts = InMemoryHostStore()
    token = create_host_jwt(sk, aud=["aud1", "aud2"])
    res = await verify_host_jwt(token, hosts, expected_audience=["aud3", "aud4"])
    assert not res.ok
    assert res.error == "audience mismatch"


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
def test_agent_jwt_round_trip_list_audience() -> None:
    agent_sk = Ed25519PrivateKey.generate()
    host_tp = "BeHE0RFM9jC46s0RCLfWvd-yfBVwRzIYZ_fp_IpsoUs"
    token = create_agent_jwt(
        agent_sk,
        host_thumbprint=host_tp,
        agent_id="agent-urn-1",
        aud=["aud-a", "aud-b"],
        capabilities=["asap:read"],
    )
    pub = _public_okp(agent_sk)
    decoded = jose_jwt.decode(token, pub, algorithms=JWT_ALGS_VERIFY)
    assert dict(decoded.claims)["aud"] == ["aud-a", "aud-b"]


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
async def test_verify_host_jwt_dynamic_registration() -> None:
    """Unknown thumbprint in store still verifies signature; host is None."""
    sk = Ed25519PrivateKey.generate()
    hosts = InMemoryHostStore()
    token = create_host_jwt(sk, aud="dyn")
    res = await verify_host_jwt(token, hosts)
    assert res.ok
    assert res.host is None
    assert res.claims is not None


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
async def test_verify_host_jwt_revoked_host_rejected() -> None:
    """Stored host in revoked state rejects the token."""
    now = datetime.now(timezone.utc)
    sk = Ed25519PrivateKey.generate()
    pub = _public_jwk_dict(sk)
    hosts = InMemoryHostStore()
    await hosts.save(
        HostIdentity(
            host_id="h1",
            public_key=pub,
            status="revoked",
            created_at=now,
            updated_at=now,
        )
    )
    token = create_host_jwt(sk, aud="x")
    res = await verify_host_jwt(token, hosts)
    assert not res.ok
    assert res.error == "host revoked"


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
async def test_verify_agent_jwt_success() -> None:
    """Valid agent JWT with matching host and active agent."""
    now = datetime.now(timezone.utc)
    host_sk = Ed25519PrivateKey.generate()
    host_pub = _public_jwk_dict(host_sk)
    host_tp = jwk_thumbprint_sha256(host_pub)
    agent_sk = Ed25519PrivateKey.generate()
    agent_pub = _public_jwk_dict(agent_sk)

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
    token = create_agent_jwt(agent_sk, host_thumbprint=host_tp, agent_id="a1", aud="aud")
    res = await verify_agent_jwt(token, hosts, agents)
    assert res.ok
    assert res.agent is not None and res.agent.agent_id == "a1"
    assert res.host is not None and res.host.host_id == "h1"


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
async def test_verify_agent_jwt_persists_extended_session() -> None:
    """Successful verification must persist sliding ``last_used_at`` for session_ttl."""
    now = datetime.now(timezone.utc)
    host_sk = Ed25519PrivateKey.generate()
    host_pub = _public_jwk_dict(host_sk)
    host_tp = jwk_thumbprint_sha256(host_pub)
    agent_sk = Ed25519PrivateKey.generate()
    agent_pub = _public_jwk_dict(agent_sk)

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
    stale_last_used = now - timedelta(minutes=30)
    await agents.save(
        AgentSession(
            agent_id="a1",
            host_id="h1",
            public_key=agent_pub,
            mode="delegated",
            status="active",
            created_at=now,
            session_ttl=timedelta(hours=1),
            last_used_at=stale_last_used,
        )
    )
    token = create_agent_jwt(agent_sk, host_thumbprint=host_tp, agent_id="a1", aud="aud")
    res = await verify_agent_jwt(token, hosts, agents)
    assert res.ok
    stored = await agents.get("a1")
    assert stored is not None
    assert stored.last_used_at is not None
    assert stored.last_used_at > stale_last_used


class _RevokeOnSecondGetAgentStore(InMemoryAgentStore):
    """Simulates a concurrent revoke between verify's initial and pre-save reads."""

    def __init__(self) -> None:
        super().__init__()
        self._get_calls = 0

    async def get(self, agent_id: str) -> AgentSession | None:
        self._get_calls += 1
        if self._get_calls >= 2:
            await self.revoke(agent_id)
        return await super().get(agent_id)


class _RotateOnSecondGetAgentStore(InMemoryAgentStore):
    """Simulates a concurrent key rotation between verify's initial and pre-touch reads."""

    def __init__(self, rotated_key: dict[str, Any]) -> None:
        super().__init__()
        self._get_calls = 0
        self._rotated_key = rotated_key

    async def get(self, agent_id: str) -> AgentSession | None:
        self._get_calls += 1
        if self._get_calls >= 2:
            current = self._agents.get(agent_id)
            if current is not None:
                self._agents[agent_id] = current.model_copy(
                    update={"public_key": self._rotated_key}
                )
        return await super().get(agent_id)


class _RehostOnSecondGetAgentStore(InMemoryAgentStore):
    """Simulates host_id changing between verify's initial and pre-touch reads."""

    def __init__(self) -> None:
        super().__init__()
        self._get_calls = 0

    async def get(self, agent_id: str) -> AgentSession | None:
        self._get_calls += 1
        if self._get_calls >= 2:
            current = self._agents.get(agent_id)
            if current is not None:
                self._agents[agent_id] = current.model_copy(update={"host_id": "other-host"})
        return await super().get(agent_id)


class _RevokeAtTouchStartAgentStore(InMemoryAgentStore):
    """Revokes at the start of touch so a full-row save would resurrect the session."""

    async def touch_if_current(
        self,
        agent_id: str,
        expected_public_key: dict[str, Any],
        last_used_at: datetime,
        *,
        expected_host_id: str,
    ) -> AgentSession | None:
        await self.revoke(agent_id)
        return await super().touch_if_current(
            agent_id,
            expected_public_key,
            last_used_at,
            expected_host_id=expected_host_id,
        )


class _RotateAtTouchStartAgentStore(InMemoryAgentStore):
    """Rotates at the start of touch so a full-row save would roll back the new key."""

    def __init__(self, rotated_key: dict[str, Any]) -> None:
        super().__init__()
        self._rotated_key = rotated_key

    async def touch_if_current(
        self,
        agent_id: str,
        expected_public_key: dict[str, Any],
        last_used_at: datetime,
        *,
        expected_host_id: str,
    ) -> AgentSession | None:
        current = self._agents.get(agent_id)
        if current is not None:
            self._agents[agent_id] = current.model_copy(update={"public_key": self._rotated_key})
        return await super().touch_if_current(
            agent_id,
            expected_public_key,
            last_used_at,
            expected_host_id=expected_host_id,
        )


async def _seed_verify_agent_jwt(
    agents: InMemoryAgentStore,
) -> tuple[InMemoryHostStore, str, dict[str, Any]]:
    """Persist h1/a1 and return hosts, token, and the agent's public JWK."""
    now = datetime.now(timezone.utc)
    host_sk = Ed25519PrivateKey.generate()
    host_pub = _public_jwk_dict(host_sk)
    host_tp = jwk_thumbprint_sha256(host_pub)
    agent_sk = Ed25519PrivateKey.generate()
    agent_pub = _public_jwk_dict(agent_sk)
    hosts = InMemoryHostStore()
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
    token = create_agent_jwt(agent_sk, host_thumbprint=host_tp, agent_id="a1", aud="aud")
    return hosts, token, agent_pub


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
async def test_verify_agent_jwt_does_not_resurrect_revoked_agent() -> None:
    """Pre-touch re-read must reject when revoke wins the race over session extension."""
    agents = _RevokeOnSecondGetAgentStore()
    hosts, token, _agent_pub = await _seed_verify_agent_jwt(agents)
    res = await verify_agent_jwt(token, hosts, agents)
    assert not res.ok
    assert res.error is not None and "not usable" in res.error
    stored = await agents.get("a1")
    assert stored is not None and stored.status == "revoked"


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
async def test_verify_agent_jwt_rejects_key_rotation_on_re_read() -> None:
    """Pre-touch re-read must reject when rotate-key wins before persist."""
    rotated = _public_jwk_dict(Ed25519PrivateKey.generate())
    agents = _RotateOnSecondGetAgentStore(rotated)
    hosts, token, original = await _seed_verify_agent_jwt(agents)
    res = await verify_agent_jwt(token, hosts, agents)
    assert not res.ok
    assert res.error == "agent key changed during verification"
    stored = await agents.get("a1")
    assert stored is not None
    assert stored.public_key == rotated
    assert stored.public_key != original


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
async def test_verify_agent_jwt_rejects_host_id_change_on_re_read() -> None:
    """Pre-touch re-read must reject when host_id no longer matches the verified row."""
    agents = _RehostOnSecondGetAgentStore()
    hosts, token, _agent_pub = await _seed_verify_agent_jwt(agents)
    res = await verify_agent_jwt(token, hosts, agents)
    assert not res.ok
    assert res.error == "agent host_id changed during verification"
    stored = await agents.get("a1")
    assert stored is not None and stored.host_id == "other-host"


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
async def test_verify_agent_jwt_touch_does_not_resurrect_after_save_time_revoke() -> None:
    """Revoke at touch start must stick; a full-row save would resurrect the session."""
    agents = _RevokeAtTouchStartAgentStore()
    hosts, token, _agent_pub = await _seed_verify_agent_jwt(agents)
    res = await verify_agent_jwt(token, hosts, agents)
    assert not res.ok
    assert res.error == "agent session changed during verification"
    stored = await agents.get("a1")
    assert stored is not None and stored.status == "revoked"


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
async def test_verify_agent_jwt_touch_does_not_undo_save_time_key_rotation() -> None:
    """Rotate at touch start must stick; a full-row save would restore the old JWK."""
    rotated = _public_jwk_dict(Ed25519PrivateKey.generate())
    agents = _RotateAtTouchStartAgentStore(rotated)
    hosts, token, original = await _seed_verify_agent_jwt(agents)
    res = await verify_agent_jwt(token, hosts, agents)
    assert not res.ok
    assert res.error == "agent session changed during verification"
    stored = await agents.get("a1")
    assert stored is not None
    assert stored.public_key == rotated
    assert jwk_thumbprint_sha256(stored.public_key) != jwk_thumbprint_sha256(original)


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
async def test_verify_agent_jwt_audience_mismatch() -> None:
    """``expected_audience`` rejects tokens minted for another consumer."""
    now = datetime.now(timezone.utc)
    host_sk = Ed25519PrivateKey.generate()
    host_pub = _public_jwk_dict(host_sk)
    host_tp = jwk_thumbprint_sha256(host_pub)
    agent_sk = Ed25519PrivateKey.generate()
    agent_pub = _public_jwk_dict(agent_sk)

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
    token = create_agent_jwt(
        agent_sk,
        host_thumbprint=host_tp,
        agent_id="a1",
        aud="other-consumer",
    )
    res = await verify_agent_jwt(
        token,
        hosts,
        agents,
        expected_audience="urn:asap:expected",
    )
    assert not res.ok
    assert res.error == "audience mismatch"


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
async def test_verify_agent_jwt_unknown_host() -> None:
    """iss thumbprint not in host store."""
    now = datetime.now(timezone.utc)
    host_sk = Ed25519PrivateKey.generate()
    host_pub = _public_jwk_dict(host_sk)
    agent_sk = Ed25519PrivateKey.generate()
    agent_pub = _public_jwk_dict(agent_sk)

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
    wrong_tp = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    token = create_agent_jwt(agent_sk, host_thumbprint=wrong_tp, agent_id="a1", aud="aud")
    res = await verify_agent_jwt(token, hosts, agents)
    assert not res.ok
    assert res.error == "unknown host for iss"


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
async def test_verify_agent_jwt_revoked_agent() -> None:
    """Agent session revoked is rejected before signature checks complete."""
    now = datetime.now(timezone.utc)
    host_sk = Ed25519PrivateKey.generate()
    host_pub = _public_jwk_dict(host_sk)
    host_tp = jwk_thumbprint_sha256(host_pub)
    agent_sk = Ed25519PrivateKey.generate()
    agent_pub = _public_jwk_dict(agent_sk)

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
            status="revoked",
            created_at=now,
        )
    )
    token = create_agent_jwt(agent_sk, host_thumbprint=host_tp, agent_id="a1", aud="aud")
    res = await verify_agent_jwt(token, hosts, agents)
    assert not res.ok
    assert res.error is not None and "revoked" in res.error


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
async def test_verify_agent_jwt_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    """jose_jwt rejects token when exp is in the past (wall clock advanced)."""
    now = datetime.now(timezone.utc)
    host_sk = Ed25519PrivateKey.generate()
    host_pub = _public_jwk_dict(host_sk)
    host_tp = jwk_thumbprint_sha256(host_pub)
    agent_sk = Ed25519PrivateKey.generate()
    agent_pub = _public_jwk_dict(agent_sk)

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

    t0 = 1_700_000_000.0
    monkeypatch.setattr("time.time", lambda: t0)
    token = create_agent_jwt(agent_sk, host_thumbprint=host_tp, agent_id="a1", aud="aud")

    monkeypatch.setattr("time.time", lambda: t0 + 120.0)
    res = await verify_agent_jwt(token, hosts, agents)
    assert not res.ok
    assert res.error == "token expired or missing exp"


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
async def test_verify_agent_jwt_jti_replay() -> None:
    """Second presentation of the same token fails when replay cache is enabled."""
    now = datetime.now(timezone.utc)
    host_sk = Ed25519PrivateKey.generate()
    host_pub = _public_jwk_dict(host_sk)
    host_tp = jwk_thumbprint_sha256(host_pub)
    agent_sk = Ed25519PrivateKey.generate()
    agent_pub = _public_jwk_dict(agent_sk)

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
    token = create_agent_jwt(agent_sk, host_thumbprint=host_tp, agent_id="a1", aud="aud")
    cache = JtiReplayCache()
    res1 = await verify_agent_jwt(token, hosts, agents, jti_replay_cache=cache)
    res2 = await verify_agent_jwt(token, hosts, agents, jti_replay_cache=cache)
    assert res1.ok
    assert not res2.ok
    assert res2.error == "jti replay detected"


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
async def test_verify_host_jwt_rejects_wrong_typ() -> None:
    """Agent JWT must not pass host verifier typ check."""
    now = datetime.now(timezone.utc)
    host_sk = Ed25519PrivateKey.generate()
    host_pub = _public_jwk_dict(host_sk)
    host_tp = jwk_thumbprint_sha256(host_pub)
    agent_sk = Ed25519PrivateKey.generate()
    agent_pub = _public_jwk_dict(agent_sk)

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
    agent_token = create_agent_jwt(agent_sk, host_thumbprint=host_tp, agent_id="a1", aud="aud")
    res = await verify_host_jwt(agent_token, hosts)
    assert not res.ok
    assert res.error == "invalid typ for host JWT"


def test_jti_replay_cache_same_jti_allowed_after_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    """After the TTL window, the same jti may be used again (partitioned key)."""
    cache = JtiReplayCache(ttl_seconds=1.0)
    t0 = 10_000.0
    monkeypatch.setattr("asap.auth.agent_jwt.time.time", lambda: t0)
    assert cache.check_and_record("part", "jti-1")
    assert not cache.check_and_record("part", "jti-1")
    monkeypatch.setattr("asap.auth.agent_jwt.time.time", lambda: t0 + 2.0)
    assert cache.check_and_record("part", "jti-1")


def test_jti_replay_cache_contains_respects_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    """``contains()`` reports only unexpired replay keys for a partition."""
    cache = JtiReplayCache(ttl_seconds=1.0)
    t0 = 20_000.0
    monkeypatch.setattr("asap.auth.agent_jwt.time.time", lambda: t0)
    assert not cache.contains("part", "jti-1")
    assert cache.check_and_record("part", "jti-1")
    assert cache.contains("part", "jti-1")
    monkeypatch.setattr("asap.auth.agent_jwt.time.time", lambda: t0 + 2.0)
    assert not cache.contains("part", "jti-1")


def test_jti_replay_cache_rejects_blank_jti() -> None:
    """Empty or whitespace ``jti`` must not be recorded as a replay key."""
    cache = JtiReplayCache()
    assert not cache.check_and_record("p", "")
    assert not cache.check_and_record("p", "   ")


def test_jti_replay_cache_max_size_evicts_oldest_expiry_first() -> None:
    """Beyond ``max_size``, earliest-expiring entries are dropped (memory cap)."""
    cache = JtiReplayCache(ttl_seconds=90.0, max_size=2)
    assert cache.check_and_record("p", "a")
    assert cache.check_and_record("p", "b")
    assert len(cache._expiry_by_key) == 2
    assert cache.check_and_record("p", "c")
    assert len(cache._expiry_by_key) == 2


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
async def test_verify_host_jwt_malformed_not_three_segments() -> None:
    """Non-JWT string returns structured error."""
    hosts = InMemoryHostStore()
    res = await verify_host_jwt("not-a-jwt", hosts)
    assert not res.ok
    assert res.error is not None and "invalid JWT structure" in res.error


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
async def test_verify_host_jwt_invalid_signature() -> None:
    """Corrupted signature fails Jose decode path."""
    sk = Ed25519PrivateKey.generate()
    token = create_host_jwt(sk, aud="x")
    parts = token.split(".")
    sig = parts[2]
    # Must change at least one base64url character; a fixed "X" is a no-op when
    # the signature already starts with "X" (~1/64), which flakes in CI.
    flip = "A" if not sig or sig[0] != "A" else "B"
    parts[2] = flip + sig[1:] if len(sig) > 1 else flip
    bad = ".".join(parts)
    res = await verify_host_jwt(bad, InMemoryHostStore())
    assert not res.ok
    assert res.error is not None and "invalid host JWT" in res.error


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
async def test_verify_host_jwt_missing_host_public_key_in_unverified() -> None:
    """Host JWT without ``host_public_key`` in payload fails before verify."""
    sk = Ed25519PrivateKey.generate()
    okp = _ed25519_to_okp_signing_key(sk)
    now = int(time.time())
    token = jose_jwt.encode(
        {"alg": JWT_ALGS_SIGN, "typ": HOST_JWT_TYP},
        {"iss": "x", "aud": "a", "iat": now, "exp": now + 300, "jti": "j1"},
        okp,
        algorithms=[JWT_ALGS_SIGN],
    )
    res = await verify_host_jwt(token, InMemoryHostStore())
    assert not res.ok
    assert res.error == "missing or invalid host_public_key claim"


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
async def test_verify_host_jwt_allows_missing_iat() -> None:
    """When ``iat`` is absent, verification skips future-iat check (RFC leniency)."""
    sk = Ed25519PrivateKey.generate()
    okp = _ed25519_to_okp_signing_key(sk)
    host_pub = dict(okp.as_dict(private=False))
    iss = jwk_thumbprint_sha256(host_pub)
    now = int(time.time())
    token = jose_jwt.encode(
        {"alg": JWT_ALGS_SIGN, "typ": HOST_JWT_TYP},
        {
            "iss": iss,
            "aud": "a",
            "exp": now + 300,
            "jti": "j-no-iat",
            HOST_PUBLIC_KEY_CLAIM: host_pub,
        },
        okp,
        algorithms=[JWT_ALGS_SIGN],
    )
    res = await verify_host_jwt(token, InMemoryHostStore())
    assert res.ok


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
async def test_verify_host_jwt_invalid_iat_type() -> None:
    """Non-numeric ``iat`` fails the clock skew check."""
    sk = Ed25519PrivateKey.generate()
    okp = _ed25519_to_okp_signing_key(sk)
    host_pub = dict(okp.as_dict(private=False))
    iss = jwk_thumbprint_sha256(host_pub)
    now = int(time.time())
    token = jose_jwt.encode(
        {"alg": JWT_ALGS_SIGN, "typ": HOST_JWT_TYP},
        {
            "iss": iss,
            "aud": "a",
            "iat": "not-a-number",
            "exp": now + 300,
            "jti": "j-bad-iat",
            HOST_PUBLIC_KEY_CLAIM: host_pub,
        },
        okp,
        algorithms=[JWT_ALGS_SIGN],
    )
    res = await verify_host_jwt(token, InMemoryHostStore())
    assert not res.ok
    assert res.error == "invalid iat (too far in the future)"


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
async def test_verify_host_jwt_invalid_exp_type() -> None:
    """Non-numeric ``exp`` fails expiry validation."""
    sk = Ed25519PrivateKey.generate()
    okp = _ed25519_to_okp_signing_key(sk)
    host_pub = dict(okp.as_dict(private=False))
    iss = jwk_thumbprint_sha256(host_pub)
    now = int(time.time())
    token = jose_jwt.encode(
        {"alg": JWT_ALGS_SIGN, "typ": HOST_JWT_TYP},
        {
            "iss": iss,
            "aud": "a",
            "iat": now,
            "exp": "bad-exp",
            "jti": "j-bad-exp",
            HOST_PUBLIC_KEY_CLAIM: host_pub,
        },
        okp,
        algorithms=[JWT_ALGS_SIGN],
    )
    res = await verify_host_jwt(token, InMemoryHostStore())
    assert not res.ok
    assert res.error == "token expired or missing exp"


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
async def test_verify_host_jwt_missing_exp_claim() -> None:
    """Missing ``exp`` is rejected after signature verification."""
    sk = Ed25519PrivateKey.generate()
    okp = _ed25519_to_okp_signing_key(sk)
    host_pub = dict(okp.as_dict(private=False))
    iss = jwk_thumbprint_sha256(host_pub)
    now = int(time.time())
    token = jose_jwt.encode(
        {"alg": JWT_ALGS_SIGN, "typ": HOST_JWT_TYP},
        {
            "iss": iss,
            "aud": "a",
            "iat": now,
            "jti": "j-exp-missing",
            HOST_PUBLIC_KEY_CLAIM: host_pub,
        },
        okp,
        algorithms=[JWT_ALGS_SIGN],
    )
    res = await verify_host_jwt(token, InMemoryHostStore())
    assert not res.ok
    assert res.error == "token expired or missing exp"


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
async def test_verify_host_jwt_iat_too_far_in_future(monkeypatch: pytest.MonkeyPatch) -> None:
    """``iat`` beyond allowed skew is rejected."""
    sk = Ed25519PrivateKey.generate()
    okp = _ed25519_to_okp_signing_key(sk)
    host_pub = dict(okp.as_dict(private=False))
    iss = jwk_thumbprint_sha256(host_pub)
    t0 = 2_000_000_000
    monkeypatch.setattr("time.time", lambda: float(t0))
    token = jose_jwt.encode(
        {"alg": JWT_ALGS_SIGN, "typ": HOST_JWT_TYP},
        {
            "iss": iss,
            "aud": "a",
            "iat": t0 + 120,
            "exp": t0 + 400,
            "jti": "j-iat-future",
            HOST_PUBLIC_KEY_CLAIM: host_pub,
        },
        okp,
        algorithms=[JWT_ALGS_SIGN],
    )
    res = await verify_host_jwt(token, InMemoryHostStore())
    assert not res.ok
    assert res.error == "invalid iat (too far in the future)"


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
async def test_verify_host_jwt_missing_jti() -> None:
    """Missing ``jti`` claim fails."""
    sk = Ed25519PrivateKey.generate()
    okp = _ed25519_to_okp_signing_key(sk)
    host_pub = dict(okp.as_dict(private=False))
    iss = jwk_thumbprint_sha256(host_pub)
    now = int(time.time())
    token = jose_jwt.encode(
        {"alg": JWT_ALGS_SIGN, "typ": HOST_JWT_TYP},
        {
            "iss": iss,
            "aud": "a",
            "iat": now,
            "exp": now + 300,
            HOST_PUBLIC_KEY_CLAIM: host_pub,
        },
        okp,
        algorithms=[JWT_ALGS_SIGN],
    )
    res = await verify_host_jwt(token, InMemoryHostStore())
    assert not res.ok
    assert res.error == "missing jti"


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
async def test_verify_host_jwt_iss_mismatch_thumbprint() -> None:
    """``iss`` must match thumbprint of ``host_public_key`` in claims."""
    sk = Ed25519PrivateKey.generate()
    okp = _ed25519_to_okp_signing_key(sk)
    host_pub = dict(okp.as_dict(private=False))
    now = int(time.time())
    token = jose_jwt.encode(
        {"alg": JWT_ALGS_SIGN, "typ": HOST_JWT_TYP},
        {
            "iss": "wrong-thumbprint-value",
            "aud": "a",
            "iat": now,
            "exp": now + 300,
            "jti": "j-iss",
            HOST_PUBLIC_KEY_CLAIM: host_pub,
        },
        okp,
        algorithms=[JWT_ALGS_SIGN],
    )
    res = await verify_host_jwt(token, InMemoryHostStore())
    assert not res.ok
    assert res.error == "iss does not match host_public_key thumbprint"


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
async def test_verify_host_jwt_jti_replay_with_cache() -> None:
    """Host JWT replay is rejected when ``JtiReplayCache`` is provided."""
    now = datetime.now(timezone.utc)
    sk = Ed25519PrivateKey.generate()
    pub = _public_jwk_dict(sk)
    hosts = InMemoryHostStore()
    await hosts.save(
        HostIdentity(
            host_id="h1",
            public_key=pub,
            status="active",
            created_at=now,
            updated_at=now,
        )
    )
    token = create_host_jwt(sk, aud="https://aud.example")
    cache = JtiReplayCache()
    res1 = await verify_host_jwt(token, hosts, jti_replay_cache=cache)
    res2 = await verify_host_jwt(token, hosts, jti_replay_cache=cache)
    assert res1.ok
    assert not res2.ok
    assert res2.error == "jti replay detected"


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
async def test_verify_agent_jwt_malformed_token() -> None:
    """Malformed agent JWT string."""
    res = await verify_agent_jwt("bad", InMemoryHostStore(), InMemoryAgentStore())
    assert not res.ok
    assert res.error is not None and "invalid JWT structure" in res.error


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
async def test_verify_agent_jwt_wrong_typ() -> None:
    """Host JWT must not pass agent verifier."""
    now = datetime.now(timezone.utc)
    sk = Ed25519PrivateKey.generate()
    hosts = InMemoryHostStore()
    await hosts.save(
        HostIdentity(
            host_id="h1",
            public_key=_public_jwk_dict(sk),
            status="active",
            created_at=now,
            updated_at=now,
        )
    )
    token = create_host_jwt(sk, aud="x")
    res = await verify_agent_jwt(token, hosts, InMemoryAgentStore())
    assert not res.ok
    assert res.error == "invalid typ for agent JWT"


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
async def test_verify_agent_jwt_missing_sub_in_payload() -> None:
    """Unverified payload must include non-empty ``sub``."""
    agent_sk = Ed25519PrivateKey.generate()
    okp = _ed25519_to_okp_signing_key(agent_sk)
    now = int(time.time())
    token = jose_jwt.encode(
        {"alg": JWT_ALGS_SIGN, "typ": AGENT_JWT_TYP},
        {
            "iss": "host-tp",
            "aud": "a",
            "iat": now,
            "exp": now + 60,
            "jti": "j1",
        },
        okp,
        algorithms=[JWT_ALGS_SIGN],
    )
    res = await verify_agent_jwt(token, InMemoryHostStore(), InMemoryAgentStore())
    assert not res.ok
    assert res.error == "missing sub (agent id)"


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
async def test_verify_agent_jwt_unknown_agent_id() -> None:
    """No agent row for ``sub``."""
    host_sk = Ed25519PrivateKey.generate()
    host_pub = _public_jwk_dict(host_sk)
    host_tp = jwk_thumbprint_sha256(host_pub)
    agent_sk = Ed25519PrivateKey.generate()
    now = datetime.now(timezone.utc)
    hosts = InMemoryHostStore()
    await hosts.save(
        HostIdentity(
            host_id="h1",
            public_key=host_pub,
            status="active",
            created_at=now,
            updated_at=now,
        )
    )
    token = create_agent_jwt(agent_sk, host_thumbprint=host_tp, agent_id="missing", aud="a")
    res = await verify_agent_jwt(token, hosts, InMemoryAgentStore())
    assert not res.ok
    assert res.error == "unknown agent"


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
async def test_verify_agent_jwt_expired_session_status() -> None:
    """Agent with status ``expired`` is rejected before signature verify."""
    now = datetime.now(timezone.utc)
    host_sk = Ed25519PrivateKey.generate()
    host_pub = _public_jwk_dict(host_sk)
    host_tp = jwk_thumbprint_sha256(host_pub)
    agent_sk = Ed25519PrivateKey.generate()
    agent_pub = _public_jwk_dict(agent_sk)
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
            status="expired",
            created_at=now,
        )
    )
    token = create_agent_jwt(agent_sk, host_thumbprint=host_tp, agent_id="a1", aud="a")
    res = await verify_agent_jwt(token, hosts, agents)
    assert not res.ok
    assert res.error is not None and "expired" in res.error


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
async def test_verify_agent_jwt_invalid_signature() -> None:
    """Wrong signing key for agent JWT."""
    host_sk = Ed25519PrivateKey.generate()
    host_pub = _public_jwk_dict(host_sk)
    host_tp = jwk_thumbprint_sha256(host_pub)
    agent_sk_a = Ed25519PrivateKey.generate()
    agent_sk_b = Ed25519PrivateKey.generate()
    now = datetime.now(timezone.utc)
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
            public_key=_public_jwk_dict(agent_sk_a),
            mode="delegated",
            status="active",
            created_at=now,
        )
    )
    token = create_agent_jwt(agent_sk_b, host_thumbprint=host_tp, agent_id="a1", aud="a")
    res = await verify_agent_jwt(token, hosts, agents)
    assert not res.ok
    assert res.error is not None and "invalid agent JWT" in res.error


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
async def test_verify_agent_jwt_missing_jti() -> None:
    """Agent JWT without ``jti`` fails after decode."""
    now = datetime.now(timezone.utc)
    host_sk = Ed25519PrivateKey.generate()
    host_pub = _public_jwk_dict(host_sk)
    host_tp = jwk_thumbprint_sha256(host_pub)
    agent_sk = Ed25519PrivateKey.generate()
    agent_pub = _public_jwk_dict(agent_sk)
    okp = _ed25519_to_okp_signing_key(agent_sk)
    t0 = int(time.time())
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
    token = jose_jwt.encode(
        {"alg": JWT_ALGS_SIGN, "typ": AGENT_JWT_TYP},
        {
            "iss": host_tp,
            "sub": "a1",
            "aud": "a",
            "iat": t0,
            "exp": t0 + 60,
        },
        okp,
        algorithms=[JWT_ALGS_SIGN],
    )
    res = await verify_agent_jwt(token, hosts, agents)
    assert not res.ok
    assert res.error == "missing jti"


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
async def test_verify_agent_jwt_host_revoked() -> None:
    """Host in ``revoked`` status rejects agent JWT."""
    now = datetime.now(timezone.utc)
    host_sk = Ed25519PrivateKey.generate()
    host_pub = _public_jwk_dict(host_sk)
    host_tp = jwk_thumbprint_sha256(host_pub)
    agent_sk = Ed25519PrivateKey.generate()
    agent_pub = _public_jwk_dict(agent_sk)
    hosts = InMemoryHostStore()
    agents = InMemoryAgentStore()
    await hosts.save(
        HostIdentity(
            host_id="h1",
            public_key=host_pub,
            status="revoked",
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
    token = create_agent_jwt(agent_sk, host_thumbprint=host_tp, agent_id="a1", aud="a")
    res = await verify_agent_jwt(token, hosts, agents)
    assert not res.ok
    assert res.error == "host revoked"


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
async def test_verify_agent_jwt_agent_host_id_mismatch() -> None:
    """``iss`` resolves to a host whose ``host_id`` does not match the agent session."""
    now = datetime.now(timezone.utc)
    host_sk = Ed25519PrivateKey.generate()
    host_pub = _public_jwk_dict(host_sk)
    host_tp = jwk_thumbprint_sha256(host_pub)
    agent_sk = Ed25519PrivateKey.generate()
    agent_pub = _public_jwk_dict(agent_sk)
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
            host_id="h-other",
            public_key=agent_pub,
            mode="delegated",
            status="active",
            created_at=now,
        )
    )
    token = create_agent_jwt(agent_sk, host_thumbprint=host_tp, agent_id="a1", aud="a")
    res = await verify_agent_jwt(token, hosts, agents)
    assert not res.ok
    assert res.error == "agent host_id does not match iss host"


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
async def test_verify_agent_jwt_missing_iss_string() -> None:
    """Empty ``iss`` after decode is rejected."""
    now = datetime.now(timezone.utc)
    host_sk = Ed25519PrivateKey.generate()
    host_pub = _public_jwk_dict(host_sk)
    agent_sk = Ed25519PrivateKey.generate()
    agent_pub = _public_jwk_dict(agent_sk)
    okp = _ed25519_to_okp_signing_key(agent_sk)
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
    t0 = int(time.time())
    token = jose_jwt.encode(
        {"alg": JWT_ALGS_SIGN, "typ": AGENT_JWT_TYP},
        {
            "iss": "",
            "sub": "a1",
            "aud": "a",
            "iat": t0,
            "exp": t0 + 60,
            "jti": "j1",
        },
        okp,
        algorithms=[JWT_ALGS_SIGN],
    )
    res = await verify_agent_jwt(token, hosts, agents)
    assert not res.ok
    assert res.error == "missing iss (host thumbprint)"


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
async def test_verify_agent_jwt_iat_too_far_future(monkeypatch: pytest.MonkeyPatch) -> None:
    """Agent JWT with ``iat`` in the future beyond skew is rejected."""
    now = datetime.now(timezone.utc)
    host_sk = Ed25519PrivateKey.generate()
    host_pub = _public_jwk_dict(host_sk)
    host_tp = jwk_thumbprint_sha256(host_pub)
    agent_sk = Ed25519PrivateKey.generate()
    agent_pub = _public_jwk_dict(agent_sk)
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
    t0 = 2_000_000_000
    monkeypatch.setattr("time.time", lambda: float(t0))
    okp = _ed25519_to_okp_signing_key(agent_sk)
    token = jose_jwt.encode(
        {"alg": JWT_ALGS_SIGN, "typ": AGENT_JWT_TYP},
        {
            "iss": host_tp,
            "sub": "a1",
            "aud": "a",
            "iat": t0 + 120,
            "exp": t0 + 180,
            "jti": "j-iat",
        },
        okp,
        algorithms=[JWT_ALGS_SIGN],
    )
    res = await verify_agent_jwt(token, hosts, agents)
    assert not res.ok
    assert res.error == "invalid iat (too far in the future)"
