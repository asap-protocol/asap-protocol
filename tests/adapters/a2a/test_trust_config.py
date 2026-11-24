"""The trust configuration for ingested Agent Cards.

A bad configuration is refused at construction, and the error never echoes a
pinned key back.
"""

from __future__ import annotations

from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from joserfc.jwk import ECKey
from pydantic import ValidationError

from asap.adapters.a2a import A2ATrustConfig

_PUBLIC_JWK = {
    **ECKey.import_key(ec.generate_private_key(ec.SECP256R1()).public_key()).as_dict(),
    "kid": "k1",
}
_SECRET = "c2VjcmV0c2VjcmV0c2VjcmV0c2VjcmV0"

_BAD_CONFIGS = {
    "private pinned key": {"pinned_keys": {"k1": {**_PUBLIC_JWK, "d": _SECRET}}},
    "symmetric pinned key": {"pinned_keys": {"k1": {"kty": "oct", "k": _SECRET}}},
    "pinned kid mismatch": {"pinned_keys": {"other": _PUBLIC_JWK}},
    "pinned key not a JWK": {"pinned_keys": {"k1": {"kty": "EC", "x": _SECRET}}},
    "http jku": {"jku_allowlist": ["http://keys.example.com/jwks.json"]},
    "loopback jku": {"jku_allowlist": ["https://127.0.0.1/jwks.json"]},
    "localhost jku": {"jku_allowlist": ["https://localhost/jwks.json"]},
    "jku with user info": {"jku_allowlist": [f"https://agent:{_SECRET}@keys.example.com/jwks"]},
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

    assert (config.pinned_keys, config.jku_allowlist) == ({}, frozenset())
    assert config.policy == "require_signature"
    assert config.algorithms == ("ES256", "RS256", "EdDSA", "Ed25519")
    assert config.allow_private_hosts is False


def test_a_valid_config_keeps_its_pinned_keys_and_allowlist() -> None:
    jku = "https://keys.example.com/.well-known/jwks.json"

    config = A2ATrustConfig(
        pinned_keys={"k1": _PUBLIC_JWK}, jku_allowlist=[jku], algorithms=["ES256"]
    )

    assert config.pinned_keys == {"k1": _PUBLIC_JWK}
    assert config.jku_allowlist == frozenset({jku})


def test_private_hosts_are_allowed_in_the_allowlist_only_with_the_explicit_switch() -> None:
    loopback = "https://127.0.0.1:8443/jwks.json"

    config = A2ATrustConfig(jku_allowlist=[loopback], allow_private_hosts=True)

    assert config.jku_allowlist == frozenset({loopback})
    with pytest.raises(ValidationError, match="host_not_public"):
        A2ATrustConfig(jku_allowlist=[loopback])


def test_the_config_is_frozen() -> None:
    config = A2ATrustConfig()

    with pytest.raises(ValidationError):
        config.allow_private_hosts = True
