"""Delegation allow-lists accept only the exact issuer URN.

The distant rejection ``urn:asap:agent:other`` still passes if membership
becomes case-insensitive, trimmed, or a substring check.
"""

from __future__ import annotations

from datetime import datetime, timezone

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from asap.economics.delegation import (
    DelegationConstraints,
    create_delegation_jwt,
    validate_delegation,
)

_EXACT = "urn:asap:agent:root"
_ALLOWED = frozenset({_EXACT})
_NEIGHBORS: tuple[str, ...] = (
    "urn:asap:agent:root-extra",
    "urn:asap:agent:ro",
    "URN:asap:agent:root",
    "urn:asap:agent:root ",
    " urn:asap:agent:root",
)


def _constraints() -> DelegationConstraints:
    """Constraints that are still valid in 2030."""
    return DelegationConstraints(expires_at=datetime(2030, 6, 1, tzinfo=timezone.utc))


def _token(private_key: Ed25519PrivateKey, delegator_urn: str) -> str:
    """Sign a task.execute token whose issuer is ``delegator_urn``."""
    return create_delegation_jwt(
        delegator_urn=delegator_urn,
        delegate_urn="urn:asap:agent:child",
        scopes=["task.execute"],
        constraints=_constraints(),
        private_key=private_key,
        token_id=f"del_{delegator_urn.strip() or 'blank'}",
    )


def test_allowed_delegators_match_is_exact() -> None:
    """A lookalike issuer stays out of the allow-set. The listed issuer passes."""
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    def resolve(_issuer: str) -> Ed25519PublicKey:
        return public_key

    accepted = validate_delegation(
        _token(private_key, _EXACT),
        "task.execute",
        public_key_resolver=resolve,
        allowed_delegators=set(_ALLOWED),
    )
    assert accepted.success is True
    assert accepted.delegator == _EXACT

    for issuer in _NEIGHBORS:
        result = validate_delegation(
            _token(private_key, issuer),
            "task.execute",
            public_key_resolver=resolve,
            allowed_delegators=set(_ALLOWED),
        )
        assert result.success is False
        assert result.error == "Delegator not allowed to issue delegations"
