"""Verify external access tokens and derive the external principal (v2.6.0).

Implements COMPAT-001. :func:`principal_from_claims` is the last step
for every external token: it runs only on claims that are already trusted (a
verified signature, or an introspection response) and checks ``aud``,
``exp``, ``nbf``, and ``sub`` before it maps the grants. There is no replay
check: an OAuth access token is sent on every request until it
expires.

Errors carry a typed reason and never the token or its signature.
"""

from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from typing import Any

from asap.auth.external_grants import map_external_grants, scope_tokens
from asap.auth.external_tokens import (
    ExternalIssuerConfig,
    ExternalPrincipal,
    ExternalTokenError,
    ExternalTokenErrorReason,
)
from asap.auth.jwks import audience_matches_expected

EXTERNAL_TOKEN_CLOCK_SKEW_SECONDS = 30
Reason = ExternalTokenErrorReason


def _numeric_date(claims: dict[str, Any], name: str) -> float | None:
    value = claims.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ExternalTokenError(Reason.MALFORMED_TOKEN, f"{name} is not a finite number")
    return float(value)


def _check_lifetime(claims: dict[str, Any]) -> datetime:
    """Check ``exp`` and ``nbf`` with a small clock skew and return the expiry."""
    now = time.time()
    exp = _numeric_date(claims, "exp")
    if exp is None:
        raise ExternalTokenError(Reason.MISSING_CLAIM, "exp is required")
    if now > exp + EXTERNAL_TOKEN_CLOCK_SKEW_SECONDS:
        raise ExternalTokenError(Reason.TOKEN_EXPIRED, f"expired at {exp:.0f}")
    nbf = _numeric_date(claims, "nbf")
    if nbf is not None and now + EXTERNAL_TOKEN_CLOCK_SKEW_SECONDS < nbf:
        raise ExternalTokenError(Reason.TOKEN_NOT_YET_VALID, f"not valid before {nbf:.0f}")
    try:
        return datetime.fromtimestamp(exp, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        raise ExternalTokenError(Reason.MALFORMED_TOKEN, "exp is out of range") from None


def principal_from_claims(
    issuer: ExternalIssuerConfig, claims: dict[str, Any]
) -> ExternalPrincipal:
    """Check ``aud``, ``exp``, ``nbf``, and ``sub`` of trusted claims and map the grants.

    Example:
        >>> principal = principal_from_claims(issuer_entry, verified_claims)
    """
    if not audience_matches_expected(claims, issuer.audience):
        raise ExternalTokenError(
            Reason.AUDIENCE_MISMATCH,
            f"aud {claims.get('aud')!r} does not name {issuer.audience!r}",
        )
    expires_at = _check_lifetime(claims)
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject.strip():
        raise ExternalTokenError(Reason.MISSING_CLAIM, "sub must be a non-empty string")
    client_id = next(
        (claims[name] for name in ("client_id", "azp") if isinstance(claims.get(name), str)),
        None,
    )
    return ExternalPrincipal(
        issuer=issuer.issuer,
        subject=subject,
        client_id=client_id,
        scopes=tuple(sorted(scope_tokens(claims))),
        expires_at=expires_at,
        grants=map_external_grants(claims, issuer.grant_rules),
    )
