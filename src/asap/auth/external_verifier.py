"""Verify external access tokens and derive the external principal (v2.6.0).

Implements COMPAT-001. :class:`ExternalTokenVerifier` keeps a fixed
order: the token type and the algorithm are checked from the header first;
then the unverified ``iss`` is looked up in the allowlist; then the signature
is verified with **that issuer's** keys only; then :func:`principal_from_claims`
checks ``aud``, ``exp``, ``nbf``, ``sub``, and the issuer's ``required_claims``
and maps the grants. So
``alg: none``, HMAC, an ASAP Agent JWT or Host JWT, and an unknown issuer are
refused before any key is looked up or fetched. There is no replay check: an
OAuth access token is sent on every request until it expires.

Opaque tokens (RFC 7662) go to the one issuer configured with
introspection, as do JWTs that name it. A JWS gets the header and issuer
checks first, so an ASAP token or a JWT of another issuer is never sent. A
bearer whose first dot-separated segment is base64url JSON (a JOSE header)
must be a three-segment JWS; any other such shape, a JWE included, is
``malformed_token`` and is never sent. Every other bearer is opaque: it names
no issuer, so it goes to the introspection issuer with this server's client
credentials whoever minted it. ``TokenIntrospector`` posts through the guarded
POST and reuses an answer for at most the issuer's
``introspection.cache_ttl_seconds`` after receipt (default 60 s).
A value of zero sends a request on every call. The verifier applies the same
claim checks as for a verified JWT. It also checks ``active`` and, when present, ``iss``.

Errors carry a typed reason and never the token or its signature.
"""

from __future__ import annotations

import base64
import json
import math
import time
from datetime import datetime, timezone
from functools import partial
from typing import Any

import httpx
from joserfc import jwt as jose_jwt
from joserfc.errors import JoseError
from joserfc.jws import extract_compact

from asap.auth.external_grants import claim_holds_value, map_external_grants, scope_tokens
from asap.auth.external_keys import IssuerKeySource
from asap.auth.external_tokens import (
    ExternalIssuerConfig,
    ExternalPrincipal,
    ExternalTokenConfig,
    ExternalTokenError,
    ExternalTokenErrorReason,
)
from asap.auth.introspection import TokenIntrospector
from asap.auth.jwks import audience_matches_expected, issuer_matches_expected
from asap.utils.guarded_fetch import AddressResolver, GuardedFetchError, guarded_post_form_json

# Asymmetric JWS algorithms only (RFC 8725 §3.1): "none" and HS* never reach a key lookup.
ALLOWED_EXTERNAL_JWT_ALGORITHMS = frozenset(
    {"RS256", "RS384", "RS512", "PS256", "PS384", "PS512"}
    | {"ES256", "ES384", "ES512", "EdDSA", "Ed25519"}
)
EXTERNAL_TOKEN_CLOCK_SKEW_SECONDS = 30
# ASAP's own token types never pass as external access tokens.
_ASAP_TOKEN_TYPES = frozenset({"agent+jwt", "host+jwt"})
Reason = ExternalTokenErrorReason


def _is_jose_header(segment: str) -> bool:
    """Return True if ``segment`` is base64url of a JSON object, as a JWS or JWE header is."""
    try:
        decoded = base64.b64decode(segment + "=" * (-len(segment) % 4), b"-_", validate=True)
        return isinstance(json.loads(decoded), dict)
    except ValueError:
        return False
    except RecursionError:
        # Nested past about 10,000 levels: no opaque token looks like that; never send it.
        return True


def _extract_compact_with_header(token: str) -> tuple[dict[str, Any], bytes]:
    """Read the unverified JWS header and require a JSON object."""
    try:
        compact = extract_compact(token.encode("utf-8"))
        header = compact.headers()
    except (JoseError, UnicodeError, TypeError, ValueError, RecursionError) as exc:
        raise ExternalTokenError(
            Reason.MALFORMED_TOKEN, f"not a usable JWS compact token ({type(exc).__name__})"
        ) from None
    if not isinstance(header, dict):
        raise ExternalTokenError(Reason.MALFORMED_TOKEN, "the JWS header is not an object")
    return header, compact.payload


def _unverified_parts(token: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Return the header and payload of a JWS compact token, or None for an opaque token.

    Any bearer with three dot-separated segments counts as a JWS, even one
    joserfc refuses (its header limit is 512 bytes), so it is never sent to
    the introspection issuer when it names another issuer. A bearer whose
    first segment is a JOSE header but that has another number of segments
    (a JWE, or a JWS with a segment added or removed) is malformed, not
    opaque, for the same reason.
    """
    if token.count(".") != 2:
        if _is_jose_header(token.split(".", 1)[0]):
            raise ExternalTokenError(
                Reason.MALFORMED_TOKEN, "a JOSE header, but not a three-segment JWS"
            )
        return None
    header, raw_payload = _extract_compact_with_header(token)
    try:
        payload = json.loads(raw_payload)
    except (ValueError, RecursionError) as exc:
        # ``from None``: a chained exception could print part of the token.
        raise ExternalTokenError(
            Reason.MALFORMED_TOKEN, f"not a usable JWS compact token ({type(exc).__name__})"
        ) from None
    if not isinstance(payload, dict):
        raise ExternalTokenError(Reason.MALFORMED_TOKEN, "the JWT payload is not an object")
    return header, payload


def _check_header(header: dict[str, Any]) -> None:
    typ = header.get("typ")
    if isinstance(typ, str) and typ.lower().removeprefix("application/") in _ASAP_TOKEN_TYPES:
        raise ExternalTokenError(Reason.TOKEN_TYPE_REJECTED, f"typ {typ!r} is an ASAP token")
    alg = header.get("alg")
    # Type first: a list or an object in ``alg`` is unhashable and would raise
    # TypeError in the set lookup. ``typ`` and ``kid`` are type-checked where read.
    if not isinstance(alg, str) or alg not in ALLOWED_EXTERNAL_JWT_ALGORITHMS:
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


def _check_required_claims(issuer: ExternalIssuerConfig, claims: dict[str, Any]) -> None:
    """Refuse a token without the claim values that bind it to this server (ADR-17)."""
    for name, expected in issuer.required_claims.items():
        # The message names the claim and the expected value, never the token's value.
        if claims.get(name) is None:
            raise ExternalTokenError(Reason.MISSING_CLAIM, f"required claim {name!r} is missing")
        if not claim_holds_value(claims, name, expected):
            raise ExternalTokenError(
                Reason.CLAIM_MISMATCH, f"required claim {name!r} does not hold {expected!r}"
            )


def principal_from_claims(
    issuer: ExternalIssuerConfig, claims: dict[str, Any]
) -> ExternalPrincipal:
    """Check ``aud``, ``exp``, ``nbf``, ``sub``, and the issuer's ``required_claims``; map grants.

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
    _check_required_claims(issuer, claims)
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

    One instance per app holds the key cache for each issuer.
    Include a key ID (``kid``) in JWTs when the issuer publishes multiple keys.
    Before a key rotation, check that the issuer supplies key IDs.
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
        # The config allows at most one introspection issuer: an opaque token names none.
        self._introspection = next(
            (
                (entry, _guarded_introspector(entry, resolver, transport))
                for entry in config.issuers
                if entry.introspection is not None
            ),
            None,
        )

    async def verify(self, token: str) -> ExternalPrincipal:
        """Verify ``token`` in the fixed order of this module and return its principal."""
        parts = _unverified_parts(token)
        if parts is None:
            if self._introspection is None:
                raise ExternalTokenError(
                    Reason.MALFORMED_TOKEN, "an opaque token, and no issuer uses introspection"
                )
            return await self._introspect(token, *self._introspection)
        header, unverified = parts
        _check_header(header)
        claimed = unverified.get("iss")
        issuer = self._config.issuer_for(claimed) if isinstance(claimed, str) else None
        if issuer is None:
            raise ExternalTokenError(Reason.UNTRUSTED_ISSUER, f"iss {claimed!r} is not allowlisted")
        if self._introspection is not None and issuer is self._introspection[0]:
            return await self._introspect(token, *self._introspection)
        raw_kid = header.get("kid")
        kid = raw_kid if isinstance(raw_kid, str) else None
        keys = await self._key_sources[issuer.issuer].keys_for(kid)
        if kid is None and len(keys.keys) != 1:
            raise ExternalTokenError(
                Reason.INVALID_SIGNATURE, "kid is required when the issuer publishes multiple keys"
            )
        try:
            verified = jose_jwt.decode(token, keys, algorithms=[header["alg"]])
        except (JoseError, TypeError, ValueError) as exc:
            raise ExternalTokenError(
                Reason.INVALID_SIGNATURE,
                f"no key of {issuer.issuer!r} verifies the token ({type(exc).__name__})",
            ) from None
        return principal_from_claims(issuer, dict(verified.claims))

    async def _introspect(
        self, token: str, issuer: ExternalIssuerConfig, introspector: TokenIntrospector
    ) -> ExternalPrincipal:
        try:
            info = await introspector.introspect(token)
        except (GuardedFetchError, OverflowError, ValueError) as exc:
            detail = str(exc) if isinstance(exc, GuardedFetchError) else type(exc).__name__
            raise ExternalTokenError(
                Reason.KEY_SOURCE_UNAVAILABLE, f"introspection at {issuer.issuer!r}: {detail}"
            ) from None
        if info is None:
            raise ExternalTokenError(
                Reason.TOKEN_INACTIVE, f"{issuer.issuer!r} answers active=false"
            )
        named = info.claims.get("iss")
        if named is not None and not issuer_matches_expected(info.claims, issuer.issuer):
            raise ExternalTokenError(Reason.UNTRUSTED_ISSUER, f"introspection names iss {named!r}")
        return principal_from_claims(issuer, dict(info.claims))


def _guarded_introspector(
    issuer: ExternalIssuerConfig,
    resolver: AddressResolver | None,
    transport: httpx.AsyncBaseTransport | None,
) -> TokenIntrospector:
    """A ``TokenIntrospector`` whose RFC 7662 POST goes through the guard."""
    endpoint = issuer.introspection
    if endpoint is None:
        raise ValueError(f"issuer {issuer.issuer!r} has no introspection endpoint")
    poster = partial(
        guarded_post_form_json,
        allow_private_hosts=issuer.allow_private_hosts,
        resolver=resolver,
        transport=transport,
    )
    return TokenIntrospector(
        endpoint.url,
        endpoint.client_id,
        endpoint.client_secret.get_secret_value(),
        poster=poster,
        max_cache_ttl_seconds=endpoint.cache_ttl_seconds,
    )
