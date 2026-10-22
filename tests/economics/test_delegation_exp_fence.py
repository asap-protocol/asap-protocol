"""Lock delegation expiry at the exact ``exp`` second.

``validate_delegation`` rejects when ``datetime.now().timestamp() >= exp``.
The token's ``exp`` is left far ahead of the wall clock so joserfc's own
expiry check does not run first; this test freezes only the delegation clock.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from asap.crypto.keys import generate_keypair
from asap.economics.delegation import (
    DelegationConstraints,
    create_delegation_jwt,
    validate_delegation,
)

# Year 2096. Stays in the future for joserfc, which checks exp against the wall clock.
_EXP_TS = 4_000_000_000


def test_delegation_rejects_at_exact_exp_second(monkeypatch: pytest.MonkeyPatch) -> None:
    """A signed delegation is valid one second before exp and expired at exp."""
    private_key, public_key = generate_keypair()
    token = create_delegation_jwt(
        delegator_urn="urn:asap:agent:delegator",
        delegate_urn="urn:asap:agent:delegate",
        scopes=["task.execute"],
        constraints=DelegationConstraints(
            expires_at=datetime.fromtimestamp(_EXP_TS, tz=timezone.utc),
        ),
        private_key=private_key,
        token_id="del_exp_fence",
    )

    class _FrozenDateTime(datetime):
        current = datetime.fromtimestamp(_EXP_TS - 1, tz=timezone.utc)

        @classmethod
        def now(cls, tz: timezone | None = None) -> datetime:
            return cls.current

    monkeypatch.setattr("asap.economics.delegation.datetime", _FrozenDateTime)

    def resolve_key(_issuer: str) -> Ed25519PublicKey:
        return public_key

    fresh = validate_delegation(token, "task.execute", public_key_resolver=resolve_key)
    assert fresh.success is True
    assert fresh.error is None

    _FrozenDateTime.current = datetime.fromtimestamp(_EXP_TS, tz=timezone.utc)
    expired = validate_delegation(token, "task.execute", public_key_resolver=resolve_key)
    assert expired.success is False
    assert expired.error == "Token expired"


def test_non_positive_exp_skips_expiry_on_a_valid_signature() -> None:
    """``exp <= 0`` is cleared, so a signed token is not rejected as expired."""
    private_key, public_key = generate_keypair()
    token = create_delegation_jwt(
        delegator_urn="urn:asap:agent:delegator",
        delegate_urn="urn:asap:agent:delegate",
        scopes=["task.execute"],
        constraints=DelegationConstraints(
            expires_at=datetime.fromtimestamp(0, tz=timezone.utc),
        ),
        private_key=private_key,
        token_id="del_exp_non_positive",
    )

    def resolve_key(_issuer: str) -> Ed25519PublicKey:
        return public_key

    result = validate_delegation(token, "task.execute", public_key_resolver=resolve_key)
    assert result.success is True
    assert result.error is None
