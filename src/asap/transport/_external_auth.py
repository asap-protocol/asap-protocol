"""External access tokens on ``POST /asap/capability/execute`` (v2.6.0).

See ``docs/guides/interoperability.md`` ("External access tokens on an ASAP server").
When ``create_app`` gets an ``external_token_config``, a bearer whose JOSE
header ``typ`` is not exactly ``agent+jwt`` goes to the external verifier, and
an Agent JWT keeps its own path. A failed verifier never falls back to the
other one. A rejected token gets one 401 body for every reason, so the
response does not tell which issuers are allowlisted; the typed reason goes to
the log. Neither the response nor the log ever holds the token.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
from joserfc.errors import JoseError
from joserfc.jws import extract_compact

from asap.auth.agent_jwt import AGENT_JWT_TYP
from asap.auth.capabilities import GrantCheckResult
from asap.auth.external_tokens import (
    ExternalPrincipal,
    ExternalTokenError,
    ExternalTokenErrorReason,
)
from asap.auth.external_verifier import ExternalTokenVerifier
from asap.observability import get_logger

logger = get_logger(__name__)

EXTERNAL_TOKEN_VERIFIER_STATE_ATTR = "external_token_verifier"
INSUFFICIENT_SCOPE_CHALLENGE = 'Bearer error="insufficient_scope"'
_INVALID_TOKEN_CHALLENGE = 'Bearer error="invalid_token"'


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
        header = extract_compact(token.encode("utf-8")).headers()
    except (JoseError, UnicodeError, ValueError):
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
