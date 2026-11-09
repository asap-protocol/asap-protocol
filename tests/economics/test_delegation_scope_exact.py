"""Delegation scope checks are exact membership, not prefix or substring.

``validate_delegation`` grants an action only when ``scp`` contains that
action, or the single scope ``*``. A neighboring string must not widen the
grant.
"""

from __future__ import annotations

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
_SCOPE_DENIED = "Action not allowed by token scopes"


def _delegation_token(private_key: Ed25519PrivateKey, scopes: list[str]) -> str:
    """Sign a delegation JWT whose ``scp`` claim is ``scopes``."""
    claims: dict[str, object] = {
        "iss": "urn:asap:agent:delegator",
        "aud": "urn:asap:agent:delegate",
        "jti": "del_scope_exact",
        "iat": _ISSUED_AT,
        "exp": _EXPIRES_AT,
        "scp": scopes,
    }
    return jose_jwt.encode(
        {"alg": JWT_ALG, "typ": "JWT"},
        claims,
        ed25519_private_key_to_okp(private_key),
        algorithms=[JWT_ALG],
    )


def test_validate_delegation_scope_match_is_exact() -> None:
    """``task.execute`` does not grant ``execute``, ``task``, or a longer action."""
    private_key, public_key = generate_keypair()
    token = _delegation_token(private_key, ["task.execute"])

    def resolver(_iss: str) -> Ed25519PublicKey:
        return public_key

    allowed = validate_delegation(token, "task.execute", public_key_resolver=resolver)
    assert allowed.success is True
    assert allowed.scopes == ["task.execute"]

    for action in ("execute", "task", "task.execute.admin"):
        denied = validate_delegation(token, action, public_key_resolver=resolver)
        assert denied.success is False
        assert denied.error == _SCOPE_DENIED


def test_validate_delegation_wildcard_is_only_exact_star() -> None:
    """``*`` grants any action. ``*extra`` and a padded star do not."""
    private_key, public_key = generate_keypair()

    def resolver(_iss: str) -> Ed25519PublicKey:
        return public_key

    star = _delegation_token(private_key, ["*"])
    allowed = validate_delegation(star, "data.read", public_key_resolver=resolver)
    assert allowed.success is True

    for scopes in (["*extra"], [" * "]):
        token = _delegation_token(private_key, scopes)
        denied = validate_delegation(token, "data.read", public_key_resolver=resolver)
        assert denied.success is False
        assert denied.error == _SCOPE_DENIED
