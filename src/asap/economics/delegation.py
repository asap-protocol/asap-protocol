"""Delegation tokens for trust hierarchies (v1.3).

Allows agents to grant limited permissions to other agents via signed tokens
with scopes and constraints (e.g. max_tasks, expires_at). Uses Ed25519 from v1.2.
Tokens are standard JWT (RFC 7519) signed with EdDSA.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional, cast

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from joserfc import jwt as jose_jwt
from joserfc.errors import JoseError
from joserfc.jwk import OKPKey
from pydantic import Field

from asap.models.base import ASAPBaseModel
from asap.models.ids import generate_id

# Ed25519 (RFC 9864). EdDSA accepted for legacy verify.
JWT_ALG = "Ed25519"
JWT_ALGS_VERIFY = [JWT_ALG, "EdDSA"]

# Custom claim key for delegation constraints (max_tasks, max_cost_usd).
X_ASAP_CONSTRAINTS_CLAIM = "x-asap-constraints"

# ---------------------------------------------------------------------------
# Scope vocabulary (2.1.3)
# ---------------------------------------------------------------------------

WILDCARD_SCOPE = "*"
"""Scope that grants all permissions. Used for full delegation."""

DELEGATION_SCOPES: tuple[str, ...] = (
    "task.execute",
    "task.cancel",
    "data.read",
    "data.write",
)
"""Known delegation scopes. Extensible in future versions."""


def scope_includes_action(scopes: list[str], action: str) -> bool:
    """Return True if the given scopes allow the requested action.

    WILDCARD_SCOPE (*) permits any action; otherwise requires exact match.
    """
    if not scopes:
        return False
    if WILDCARD_SCOPE in scopes:
        return True
    return action in scopes


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValidationResult:
    success: bool
    error: Optional[str] = None
    delegator: Optional[str] = None
    delegate: Optional[str] = None
    jti: Optional[str] = None
    scopes: Optional[list[str]] = None


def _b64url_decode(payload_b64: str) -> bytes:
    pad = 4 - len(payload_b64) % 4
    if pad != 4:
        payload_b64 += "=" * pad
    return base64.urlsafe_b64decode(payload_b64)


def _ed25519_public_key_to_okp_key(public_key: Ed25519PublicKey) -> OKPKey:
    raw = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    x = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return OKPKey.import_key({"kty": "OKP", "crv": "Ed25519", "x": x})


def _parse_claim_from_jwt(token: str, claim: str) -> Optional[str]:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload_bytes = _b64url_decode(parts[1])
        claims = json.loads(payload_bytes.decode("utf-8"))
        # JSON null, arrays, numbers, bools, and strings have no claim map.
        # .get would raise AttributeError, which callers do not catch.
        if not isinstance(claims, dict):
            return None
        val = claims.get(claim)
        return str(val) if val is not None else None
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def _parse_iss_from_jwt(token: str) -> Optional[str]:
    return _parse_claim_from_jwt(token, "iss")


def get_jti_from_jwt(token: str) -> Optional[str]:
    """Parse JWT payload (unverified) to get jti. Returns None if malformed."""
    return _parse_claim_from_jwt(token, "jti")


def validate_delegation(
    token: str,
    action: str,
    *,
    public_key_resolver: Callable[[str], Ed25519PublicKey],
    usage_count_for_token: Optional[Callable[[str], int]] = None,
    allowed_delegators: Optional[set[str]] = None,
    is_revoked: Optional[Callable[[str], bool]] = None,
) -> ValidationResult:
    """Validate a delegation JWT for the given action.

    Verifies Ed25519 signature (via public_key_resolver(iss)), expiration,
    scope includes action, optional max_tasks limit via usage_count_for_token,
    optional allowed_delegators (chain / no privilege escalation), and
    optional is_revoked (reject revoked token IDs).

    Args:
        token: Raw JWT string (compact).
        action: Action to check (e.g. 'task.execute', 'data.read').
        public_key_resolver: Callable that returns the delegator's public key for a given iss URN.
        usage_count_for_token: Optional callable (jti) -> usage count; if set and token has
            max_tasks, validation fails when usage >= max_tasks.
        allowed_delegators: Optional set of URNs that are allowed to issue delegations (e.g. root
            agents). If set, iss must be in this set (no privilege escalation).
        is_revoked: Optional callable (jti) -> True if token is revoked. When True, validation fails.

    Returns:
        ValidationResult with success=True and claims, or success=False and error message.
    """
    iss = _parse_iss_from_jwt(token)
    if not iss:
        return ValidationResult(success=False, error="Invalid or malformed token")

    if allowed_delegators is not None and iss not in allowed_delegators:
        return ValidationResult(
            success=False,
            error="Delegator not allowed to issue delegations",
        )

    try:
        public_key = public_key_resolver(iss)
    except (KeyError, ValueError) as e:
        return ValidationResult(
            success=False,
            error=f"Delegator key not found or invalid: {e!s}",
        )

    okp_key = _ed25519_public_key_to_okp_key(public_key)
    try:
        decoded = jose_jwt.decode(
            token,
            okp_key,
            algorithms=JWT_ALGS_VERIFY,
        )
    except JoseError as e:
        return ValidationResult(success=False, error=f"Invalid or expired token: {e!s}")

    claims = dict(decoded.claims)
    exp = claims.get("exp")
    if exp is not None:
        try:
            exp_ts = int(exp) if isinstance(exp, (int, float)) else None
            if exp_ts is not None and exp_ts <= 0:
                exp_ts = None
            if exp_ts is not None and datetime.now(timezone.utc).timestamp() >= exp_ts:
                return ValidationResult(success=False, error="Token expired")
        except (TypeError, ValueError):
            pass

    scp = claims.get("scp")
    if not isinstance(scp, list):
        scp = []
    if not scope_includes_action(scp, action):
        return ValidationResult(success=False, error="Action not allowed by token scopes")

    jti = claims.get("jti")
    jti_str = str(jti) if jti is not None else None
    if jti_str and is_revoked is not None and is_revoked(jti_str):
        return ValidationResult(success=False, error="Token revoked")
    aud = claims.get("aud")
    if isinstance(aud, list):
        delegate = aud[0] if aud else None
    else:
        delegate = str(aud) if aud is not None else None

    x_constraints = claims.get(X_ASAP_CONSTRAINTS_CLAIM)
    if isinstance(x_constraints, dict) and usage_count_for_token is not None and jti_str:
        max_tasks = x_constraints.get("max_tasks")
        if max_tasks is not None:
            try:
                used = usage_count_for_token(jti_str)
                if used >= int(max_tasks):
                    return ValidationResult(
                        success=False,
                        error="Delegation task limit exceeded",
                    )
            except (TypeError, ValueError):
                pass

    return ValidationResult(
        success=True,
        delegator=iss,
        delegate=delegate,
        jti=jti_str,
        scopes=scp,
    )


def _ed25519_private_key_to_okp_key(private_key: Ed25519PrivateKey) -> OKPKey:
    raw_private = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    raw_public = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    # JWK base64url (RFC 4648) without padding.
    def b64url(b: bytes) -> str:
        return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")

    jwk_dict = {
        "kty": "OKP",
        "crv": "Ed25519",
        "d": b64url(raw_private),
        "x": b64url(raw_public),
    }
    return OKPKey.import_key(cast("dict[str, str | list[str]]", jwk_dict))


def create_delegation_jwt(
    delegator_urn: str,
    delegate_urn: str,
    scopes: list[str],
    constraints: DelegationConstraints,
    private_key: Ed25519PrivateKey,
    token_id: Optional[str] = None,
) -> str:
    """Create a signed JWT delegation token (RFC 7519, EdDSA).

    Claims: iss (delegator), aud (delegate), jti (id), iat, exp, scp (scopes),
    and x-asap-constraints (max_tasks, max_cost_usd).
    """
    if not scopes:
        raise ValueError("scopes must not be empty")
    jti = token_id if token_id is not None else f"del_{generate_id()}"
    now = datetime.now(timezone.utc)
    exp_dt = constraints.expires_at
    if exp_dt.tzinfo is None:
        exp_dt = exp_dt.replace(tzinfo=timezone.utc)
    iat = int(now.timestamp())
    exp = int(exp_dt.timestamp())

    x_constraints: dict[str, Optional[float | int]] = {}
    if constraints.max_tasks is not None:
        x_constraints["max_tasks"] = constraints.max_tasks
    if constraints.max_cost_usd is not None:
        x_constraints["max_cost_usd"] = constraints.max_cost_usd

    claims: dict[str, object] = {
        "iss": delegator_urn,
        "aud": delegate_urn,
        "jti": jti,
        "iat": iat,
        "exp": exp,
        "scp": scopes,
    }
    if x_constraints:
        claims[X_ASAP_CONSTRAINTS_CLAIM] = x_constraints

    header = {"alg": JWT_ALG, "typ": "JWT"}
    okp_key = _ed25519_private_key_to_okp_key(private_key)
    return jose_jwt.encode(
        header,
        claims,
        okp_key,
        algorithms=[JWT_ALG],
    )


class DelegationConstraints(ASAPBaseModel):
    """Limits attached to a delegation token.

    max_cost_usd is reserved for v3.0 (Payments). max_tasks is the primary
    limit for v2.0 (Free tier). expires_at is required for all delegations.
    """

    max_cost_usd: Optional[float] = Field(
        default=None,
        ge=0,
        description="Maximum spend in USD (reserved for v3.0 Payments).",
    )
    max_tasks: Optional[int] = Field(
        default=None,
        ge=0,
        description="Maximum number of tasks the delegate may perform (v2.0).",
    )
    expires_at: datetime = Field(
        ...,
        description="Token expiration time (UTC).",
    )


class DelegationToken(ASAPBaseModel):
    """Signed token granting limited permissions from delegator to delegate.

    The signature is produced over the canonical token payload (e.g. JCS)
    using the delegator's Ed25519 key. Validated during token verification.
    """

    id: str = Field(..., description="Unique token identifier.")
    delegator: str = Field(..., description="URN of the agent granting the delegation.")
    delegate: str = Field(..., description="URN of the agent receiving the delegation.")
    scopes: list[str] = Field(
        ...,
        min_length=1,
        description="Allowed permission scopes (e.g. task.execute, data.read).",
    )
    constraints: DelegationConstraints = Field(
        ...,
        description="Limits (max_tasks, expires_at, optional max_cost_usd).",
    )
    signature: str = Field(..., description="Ed25519 signature over the token payload.")
    created_at: datetime = Field(
        ...,
        description="Token creation time (UTC).",
    )
