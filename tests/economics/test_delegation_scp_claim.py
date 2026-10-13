"""Delegation ``scp`` must be a JSON array of scope strings.

``validate_delegation`` replaces any non-list ``scp`` with ``[]`` before the
scope check. A string that names the requested action, an object, or JSON
null must not grant that action. List-valued ``scp`` is already covered.
"""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from joserfc import jwt as jose_jwt

from asap.crypto.keys import generate_keypair
from asap.economics.delegation import JWT_ALG, validate_delegation
from asap.economics.delegation import (
    _ed25519_private_key_to_okp_key as ed25519_private_key_to_okp,
)

_ISSUED_AT = 1_700_000_000
_EXPIRES_AT = 4_000_000_000
_ACTION = "task.execute"
_SCOPE_DENIED = "Action not allowed by token scopes"


def _delegation_token(private_key: Ed25519PrivateKey, scp_claim: object) -> str:
    """Sign a delegation JWT whose ``scp`` claim is ``scp_claim``."""
    claims: dict[str, object] = {
        "iss": "urn:asap:agent:delegator",
        "aud": "urn:asap:agent:delegate",
        "jti": "del_scp_type",
        "iat": _ISSUED_AT,
        "exp": _EXPIRES_AT,
        "scp": scp_claim,
    }
    header = {"alg": JWT_ALG, "typ": "JWT"}
    return jose_jwt.encode(
        header,
        claims,
        ed25519_private_key_to_okp(private_key),
        algorithms=[JWT_ALG],
    )


def test_validate_delegation_accepts_list_scp() -> None:
    """The same signer accepts a JSON array that contains the action."""
    private_key, public_key = generate_keypair()
    token = _delegation_token(private_key, [_ACTION])

    def resolver(_iss: str) -> Ed25519PublicKey:
        return public_key

    result = validate_delegation(token, _ACTION, public_key_resolver=resolver)
    assert result.success is True
    assert result.scopes == [_ACTION]


@pytest.mark.parametrize(
    "scp_claim",
    ["task.execute", {"task.execute": True}, None],
    ids=["string", "object", "null"],
)
def test_validate_delegation_rejects_non_list_scp(scp_claim: object) -> None:
    """A non-list ``scp`` fails closed even when it names the requested action."""
    private_key, public_key = generate_keypair()
    token = _delegation_token(private_key, scp_claim)

    def resolver(_iss: str) -> Ed25519PublicKey:
        return public_key

    result = validate_delegation(token, _ACTION, public_key_resolver=resolver)
    assert result.success is False
    assert result.error == _SCOPE_DENIED
