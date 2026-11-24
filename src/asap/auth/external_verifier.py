"""Verify external access tokens and derive the external principal (v2.6.0).

Implements COMPAT-001. :class:`ExternalTokenVerifier` keeps a fixed
order: the token type and the algorithm are checked from the header first;
then the unverified ``iss`` is looked up in the allowlist; then the signature
is verified with **that issuer's** keys only; then :func:`principal_from_claims`
checks ``aud``, ``exp``, ``nbf``, and ``sub`` and maps the grants. So
``alg: none``, HMAC, an ASAP Agent JWT or Host JWT, and an unknown issuer are
refused before any key is looked up or fetched. There is no replay check: an
OAuth access token is sent on every request until it expires.

Errors carry a typed reason and never the token or its signature.
"""

from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from joserfc import jwt as jose_jwt
from joserfc.errors import JoseError
from joserfc.jws import extract_compact

from asap.auth.external_grants import map_external_grants, scope_tokens
from asap.auth.external_keys import IssuerKeySource
from asap.auth.external_tokens import (
    ExternalIssuerConfig,
    ExternalPrincipal,
    ExternalTokenConfig,
    ExternalTokenError,
    ExternalTokenErrorReason,
)
from asap.auth.jwks import audience_matches_expected
from asap.utils.guarded_fetch import AddressResolver

# Asymmetric JWS algorithms only (RFC 8725 §3.1): "none" and HS* never reach a key lookup.
ALLOWED_EXTERNAL_JWT_ALGORITHMS = frozenset(
    {"RS256", "RS384", "RS512", "PS256", "PS384", "PS512"}
    | {"ES256", "ES384", "ES512", "EdDSA", "Ed25519"}
)
EXTERNAL_TOKEN_CLOCK_SKEW_SECONDS = 30
# ASAP's own token types never pass as external access tokens.
_ASAP_TOKEN_TYPES = frozenset({"agent+jwt", "host+jwt"})
Reason = ExternalTokenErrorReason


def _unverified_parts(token: str) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        compact = extract_compact(token.encode("utf-8"))
        payload = json.loads(compact.payload)
    except (JoseError, UnicodeError, ValueError) as exc:
        # ``from None``: a chained exception could print part of the token.
        raise ExternalTokenError(
            Reason.MALFORMED_TOKEN, f"not a JWS compact token ({type(exc).__name__})"
        ) from None
    if not isinstance(payload, dict):
        raise ExternalTokenError(Reason.MALFORMED_TOKEN, "the JWT payload is not an object")
    return dict(compact.headers()), payload


def _check_header(header: dict[str, Any]) -> None:
    typ = header.get("typ")
    if isinstance(typ, str) and typ.lower().removeprefix("application/") in _ASAP_TOKEN_TYPES:
        raise ExternalTokenError(Reason.TOKEN_TYPE_REJECTED, f"typ {typ!r} is an ASAP token")
    alg = header.get("alg")
    if alg not in ALLOWED_EXTERNAL_JWT_ALGORITHMS:
        raise ExternalTokenError(
            Reason.ALGORITHM_REJECTED, f"alg {alg!r} is not an allowed asymmetric algorithm"
        )


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


class ExternalTokenVerifier:
    """Turn a bearer token into an :class:`ExternalPrincipal`, or raise :class:`ExternalTokenError`.

    One instance per app: it holds the key cache of each allowlisted issuer.
    ``resolver`` and ``transport`` are the test seams of the guarded fetch.

    Example:
        >>> principal = await ExternalTokenVerifier(config).verify(bearer_token)
    """

    def __init__(
        self,
        config: ExternalTokenConfig,
        *,
        resolver: AddressResolver | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config
        self._key_sources = {
            entry.issuer: IssuerKeySource(entry, resolver=resolver, transport=transport)
            for entry in config.issuers
        }

    async def verify(self, token: str) -> ExternalPrincipal:
        """Verify ``token`` in the fixed order of this module and return its principal."""
        header, unverified = _unverified_parts(token)
        _check_header(header)
        claimed = unverified.get("iss")
        issuer = self._config.issuer_for(claimed) if isinstance(claimed, str) else None
        if issuer is None:
            raise ExternalTokenError(Reason.UNTRUSTED_ISSUER, f"iss {claimed!r} is not allowlisted")
        kid = header.get("kid")
        keys = await self._key_sources[issuer.issuer].keys_for(
            kid if isinstance(kid, str) else None
        )
        try:
            verified = jose_jwt.decode(token, keys, algorithms=[header["alg"]])
        except (JoseError, TypeError, ValueError) as exc:
            raise ExternalTokenError(
                Reason.INVALID_SIGNATURE,
                f"no key of {issuer.issuer!r} verifies the token ({type(exc).__name__})",
            ) from None
        return principal_from_claims(issuer, dict(verified.claims))
