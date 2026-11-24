"""External access tokens on ``POST /asap/capability/execute`` (v2.6.0).

See ``docs/guides/interoperability.md`` ("External access tokens on an ASAP server").
When ``create_app`` gets an ``external_token_config``, a bearer whose JOSE
header ``typ`` is not exactly ``agent+jwt`` goes to the external verifier, and
an Agent JWT keeps its own path. A failed verifier never falls back to the
other one. A rejected token gets one 401 body for every reason, so the body
does not tell which check failed; the typed reason goes to the log. Neither
the response nor the log ever holds the token.

What the answer still shows: when the named issuer is listed but its keys (or
the introspection endpoint) cannot be had right now, the answer is 503, while
a token that names an unlisted issuer gets the 401. A key fetch can also make
the answer slower. So a caller can learn that an issuer is listed while its key
source is down. This is kept on purpose: the 503 lets a legitimate caller tell
an outage from a bad token, and the issuer list is not a secret (RFC 9728
metadata can publish it). Checking the unverified claims before the keys would
not close the gap, because a forged token can carry the right ``aud``, ``exp``,
and ``sub``.
"""

from __future__ import annotations

import os

from fastapi import Request
from fastapi.responses import JSONResponse

from asap.auth.agent_jwt import AGENT_JWT_TYP
from asap.auth.capabilities import GrantCheckResult
from asap.auth.external_tokens import (
    ExternalIssuerConfig,
    ExternalPrincipal,
    ExternalTokenConfig,
    ExternalTokenError,
    ExternalTokenErrorReason,
    JwksUriKeySource,
)
from asap.auth.external_verifier import ExternalTokenVerifier, _extract_compact_with_header
from asap.auth.jwks import issuer_matches_expected
from asap.auth.middleware import DEFAULT_CUSTOM_CLAIM, ENV_ISSUER, OAuth2Config
from asap.observability import get_logger

logger = get_logger(__name__)

EXTERNAL_TOKEN_VERIFIER_STATE_ATTR = "external_token_verifier"
INSUFFICIENT_SCOPE_CHALLENGE = 'Bearer error="insufficient_scope"'
_INVALID_TOKEN_CHALLENGE = 'Bearer error="invalid_token"'
# Read the way OAuth2Middleware reads it.
_ENV_CUSTOM_CLAIM = "ASAP_AUTH_CUSTOM_CLAIM"


def warn_if_oauth2_idp_unbound(
    config: ExternalTokenConfig, oauth2_config: OAuth2Config | None, *, manifest_id: str
) -> None:
    """Log ``asap.server.external_issuer_unbound`` for a listed IdP with no ``required_claims``.

    ``OAuth2Middleware`` skips execute, so its identity binding does not apply
    there to the tokens of the ``oauth2_config`` IdP. Listed without
    ``required_claims``, that IdP's token for another agent with the same
    audience executes here. The IdP is
    matched by ``expected_issuer`` (or ``ASAP_AUTH_ISSUER``) or by ``jwks_uri``.
    """
    if oauth2_config is None:
        return
    idp_issuer = oauth2_config.expected_issuer or os.environ.get(ENV_ISSUER)
    claim = oauth2_config.custom_claim or os.environ.get(_ENV_CUSTOM_CLAIM, DEFAULT_CUSTOM_CLAIM)
    for entry in config.issuers:
        if entry.required_claims or not _is_oauth2_idp(entry, oauth2_config.jwks_uri, idp_issuer):
            continue
        logger.warning(
            "asap.server.external_issuer_unbound",
            issuer=entry.issuer,
            required_claims={claim: manifest_id},
            message=(
                "the oauth2_config IdP is an external issuer without required_claims: its "
                "tokens for other agents execute here; set the required_claims shown"
            ),
        )


def _is_oauth2_idp(entry: ExternalIssuerConfig, jwks_uri: str, idp_issuer: str | None) -> bool:
    if idp_issuer is not None and issuer_matches_expected({"iss": entry.issuer}, idp_issuer):
        return True
    return isinstance(entry.key_source, JwksUriKeySource) and entry.key_source.jwks_uri == jwks_uri


def external_token_verifier(request: Request) -> ExternalTokenVerifier | None:
    """Return the app's external verifier, or None when the config is not set."""
    verifier: ExternalTokenVerifier | None = getattr(
        request.app.state, EXTERNAL_TOKEN_VERIFIER_STATE_ATTR, None
    )
    return verifier


def is_agent_jwt(token: str) -> bool:
    """Return True when the JOSE header ``typ`` is exactly ``agent+jwt``.

    It is the first test ``verify_agent_jwt`` applies, so the Agent JWT path
    still sees every token it could accept before.
    """
    try:
        header, _ = _extract_compact_with_header(token)
    except ExternalTokenError:
        return False
    return header.get("typ") == AGENT_JWT_TYP


async def verify_external_bearer(
    verifier: ExternalTokenVerifier, token: str, *, request_id: str
) -> ExternalPrincipal | JSONResponse:
    """Return the principal, or the 401 (token refused) or 503 (key source down) response."""
    try:
        return await verifier.verify(token)
    except ExternalTokenError as exc:
        logger.warning(
            "asap.capability.external_token_rejected",
            reason=exc.reason.value,
            detail=str(exc),
            request_id=request_id,
        )
        if exc.reason is ExternalTokenErrorReason.KEY_SOURCE_UNAVAILABLE:
            return JSONResponse(
                status_code=503,
                content={"detail": "Authentication service unavailable", "request_id": request_id},
            )
        return JSONResponse(
            status_code=401,
            content={"detail": "invalid_token", "request_id": request_id},
            headers={"WWW-Authenticate": _INVALID_TOKEN_CHALLENGE},
        )


def log_external_execute(
    principal: ExternalPrincipal, capability: str, check: GrantCheckResult, *, request_id: str
) -> None:
    """Write the audit event of one execute call by an external principal."""
    if check.allowed:
        outcome = "executed"
    else:
        outcome = "constraint_violated" if check.violations else "capability_not_granted"
    logger.info(
        "asap.capability.external_execute",
        issuer=principal.issuer,
        subject=principal.subject,
        client_id=principal.client_id,
        capability=capability,
        outcome=outcome,
        request_id=request_id,
    )
