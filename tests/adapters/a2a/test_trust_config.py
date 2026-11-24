"""The trust configuration for ingested Agent Cards.

A bad configuration is refused at construction, and the error never echoes a
pinned key back. Every pinned key and every allowed ``jku`` names the agents
it may sign for.
"""

from __future__ import annotations

from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from joserfc.jwk import ECKey
from pydantic import ValidationError

from asap.adapters.a2a import A2ATrustConfig
from asap.adapters.a2a.trust_scope import ANY_AGENT, AllowedJku, PinnedCardKey

_PUBLIC_JWK = {
    **ECKey.import_key(ec.generate_private_key(ec.SECP256R1()).public_key()).as_dict(),
    "kid": "k1",
}
_SECRET = "c2VjcmV0c2VjcmV0c2VjcmV0c2VjcmV0"
_AGENTS = {"agents": ["urn:asap:agent:bank-a"]}
_SCOPE = AllowedJku(agents=["urn:asap:agent:bank-a"])
_JKU = "https://keys.example.com/.well-known/jwks.json"


def _pinned(jwk: dict[str, Any]) -> dict[str, Any]:
    return {"jwk": jwk, **_AGENTS}


_BAD_CONFIGS = {
    "private pinned key": {"pinned_keys": {"k1": _pinned({**_PUBLIC_JWK, "d": _SECRET})}},
    "symmetric pinned key": {"pinned_keys": {"k1": _pinned({"kty": "oct", "k": _SECRET})}},
    "pinned kid mismatch": {"pinned_keys": {"other": _pinned(_PUBLIC_JWK)}},
    "pinned key not a JWK": {"pinned_keys": {"k1": _pinned({"kty": "EC", "x": _SECRET})}},
    "pinned key without agents": {"pinned_keys": {"k1": {"jwk": _PUBLIC_JWK}}},
    "pinned key with no agent": {"pinned_keys": {"k1": {"jwk": _PUBLIC_JWK, "agents": []}}},
    "jku without agents": {"jku_allowlist": {_JKU: {}}},
    "bare pinned JWK, no scope": {"pinned_keys": {"k1": _PUBLIC_JWK}},
    "jku list, no scope": {"jku_allowlist": [_JKU]},
    "http jku": {"jku_allowlist": {"http://keys.example.com/jwks.json": _AGENTS}},
    "loopback jku": {"jku_allowlist": {"https://127.0.0.1/jwks.json": _AGENTS}},
    "localhost jku": {"jku_allowlist": {"https://localhost/jwks.json": _AGENTS}},
    "jku with user info": {
        "jku_allowlist": {f"https://agent:{_SECRET}@keys.example.com/jwks": _AGENTS}
    },
    "hmac algorithm": {"algorithms": ["ES256", "HS256"]},
    "no algorithm": {"algorithms": []},
    "unknown policy": {"policy": "allow-unsigned"},
    "unknown field": {"trust_every_jku": True},
}


@pytest.mark.parametrize("settings", list(_BAD_CONFIGS.values()), ids=list(_BAD_CONFIGS))
def test_a_bad_trust_config_is_refused_without_echoing_key_material(
    settings: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError) as raised:
        A2ATrustConfig(**settings)

    assert _SECRET not in str(raised.value)


def test_the_defaults_require_a_signature_trust_nothing_and_allow_the_d3_list() -> None:
    config = A2ATrustConfig()

    assert (config.pinned_keys, config.jku_allowlist) == ({}, {})
    assert config.policy == "require_signature"
    assert config.algorithms == ("ES256", "RS256", "EdDSA", "Ed25519")
    assert config.allow_private_hosts is False


def test_a_valid_config_keeps_its_scoped_pinned_keys_and_allowlist() -> None:
    config = A2ATrustConfig(
        pinned_keys={"k1": PinnedCardKey(jwk=_PUBLIC_JWK, agents=["urn:asap:agent:bank-a"])},
        jku_allowlist={_JKU: AllowedJku(agents=ANY_AGENT)},
        algorithms=["ES256"],
    )

    assert config.pinned_keys == {"k1": PinnedCardKey(jwk=_PUBLIC_JWK, **_AGENTS)}
    assert config.jku_allowlist == {_JKU: AllowedJku(agents="any_agent")}


def test_private_hosts_are_allowed_in_the_allowlist_only_with_the_explicit_switch() -> None:
    loopback = "https://127.0.0.1:8443/jwks.json"

    config = A2ATrustConfig(jku_allowlist={loopback: _SCOPE}, allow_private_hosts=True)

    assert list(config.jku_allowlist) == [loopback]
    with pytest.raises(ValidationError, match="host_not_public"):
        A2ATrustConfig(jku_allowlist={loopback: _SCOPE})


def test_the_config_is_frozen() -> None:
    config = A2ATrustConfig()

    with pytest.raises(ValidationError):
        config.allow_private_hosts = True
