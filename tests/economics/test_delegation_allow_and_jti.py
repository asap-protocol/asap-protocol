"""Delegation allow-list acceptance and unverified jti parsing.

The rejection path for ``allowed_delegators`` does not prove a listed issuer
is accepted. Revocation keys off ``get_jti_from_jwt``, which must return None
for malformed tokens instead of raising.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from asap.economics.delegation import (
    DelegationConstraints,
    create_delegation_jwt,
    get_jti_from_jwt,
    validate_delegation,
)


def _constraints() -> DelegationConstraints:
    """Constraints that are still valid in 2030."""
    return DelegationConstraints(expires_at=datetime(2030, 6, 1, tzinfo=timezone.utc))


def _b64url(raw: bytes) -> str:
    """Base64url without padding, matching JWT segment encoding."""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def test_allowed_delegators_accepts_listed_issuer() -> None:
    """A delegator in the allow-set still passes signature and scope checks."""
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    token = create_delegation_jwt(
        delegator_urn="urn:asap:agent:root",
        delegate_urn="urn:asap:agent:child",
        scopes=["task.execute"],
        constraints=_constraints(),
        private_key=private_key,
        token_id="del_allowed_root",
    )

    result = validate_delegation(
        token,
        "task.execute",
        public_key_resolver=lambda _iss: public_key,
        allowed_delegators={"urn:asap:agent:root"},
    )

    assert result.success is True
    assert result.delegator == "urn:asap:agent:root"
    assert result.delegate == "urn:asap:agent:child"
    assert result.jti == "del_allowed_root"


def test_get_jti_from_jwt_reads_token_id_without_verifying() -> None:
    """Revocation looks up jti from the payload of a token this process signed."""
    private_key = Ed25519PrivateKey.generate()
    token = create_delegation_jwt(
        delegator_urn="urn:asap:agent:root",
        delegate_urn="urn:asap:agent:child",
        scopes=["task.execute"],
        constraints=_constraints(),
        private_key=private_key,
        token_id="del_revoke_me",
    )
    assert get_jti_from_jwt(token) == "del_revoke_me"


def test_get_jti_from_jwt_missing_claim_returns_none() -> None:
    """A compact token with no jti claim is not an id, and does not raise."""
    header = _b64url(b'{"alg":"EdDSA"}')
    payload = _b64url(b'{"iss":"urn:asap:agent:root"}')
    assert get_jti_from_jwt(f"{header}.{payload}.sig") is None


@pytest.mark.parametrize(
    "token",
    ["", "not-a-jwt", "a.b", "a.b.c.d", "@@@.@@@.@@@"],
)
def test_get_jti_from_jwt_malformed_returns_none(token: str) -> None:
    """Wrong segment counts and undecodable payloads return None."""
    assert get_jti_from_jwt(token) is None


_NON_OBJECT_PAYLOADS = (b"null", b"[]", b"42", b"true", b'"x"')


def _compact_token(payload: bytes) -> str:
    """Build an unverified compact JWT whose payload is the given JSON bytes."""
    header = _b64url(b'{"alg":"EdDSA"}')
    body = _b64url(payload)
    return f"{header}.{body}.sig"


@pytest.mark.parametrize("payload", _NON_OBJECT_PAYLOADS)
def test_get_jti_from_jwt_non_object_payload_returns_none(payload: bytes) -> None:
    """JSON null, arrays, numbers, bools, and strings are not claim maps."""
    assert get_jti_from_jwt(_compact_token(payload)) is None


@pytest.mark.parametrize("payload", _NON_OBJECT_PAYLOADS)
def test_validate_delegation_non_object_payload_is_rejected(payload: bytes) -> None:
    """A non-object payload is a rejected token, and the key resolver is not called."""

    def unexpected_key(issuer: str) -> Ed25519PublicKey:
        raise AssertionError(f"resolver called for {issuer}")

    result = validate_delegation(
        _compact_token(payload),
        "task.execute",
        public_key_resolver=unexpected_key,
    )

    assert result.success is False
    assert result.error == "Invalid or malformed token"
