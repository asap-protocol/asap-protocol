"""HTTP answers for the four ``asap:*`` cases.

Token errors are HTTP 401, a missing grant is HTTP 403 with an
``insufficient_scope`` challenge, and a constraint violation is HTTP 403 with
no challenge, as on the execute route and in ``@asap-protocol/mcp-auth``.
The ``asap:*`` code is the ``error_description`` of the JSON body, and of the
challenge when it carries an error code. No answer holds the token.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from fastapi.responses import JSONResponse

from asap.auth.capabilities import ConstraintViolation
from asap.mcp.auth.errors import (
    AUTH_REQUIRED,
    CAPABILITY_DENIED,
    CONSTRAINT_VIOLATION,
    INVALID_TOKEN,
)


def _refusal(
    status: int, error: str, description: str, challenge: str | None = None, **extra: Any
) -> JSONResponse:
    headers = {"WWW-Authenticate": challenge} if challenge else None
    body = {"error": error, "error_description": description, **extra}
    return JSONResponse(body, status_code=status, headers=headers)


def _challenge(error: str, description: str) -> str:
    return f'Bearer error="{error}", error_description="{description}"'


def auth_required() -> JSONResponse:
    """401 for a request with no bearer token; RFC 6750 §3.1: no error code in the challenge."""
    return _refusal(401, "invalid_token", AUTH_REQUIRED, "Bearer")


def invalid_token() -> JSONResponse:
    """401 for a refused token; one answer for every reason, so no issuer is revealed."""
    return _refusal(401, "invalid_token", INVALID_TOKEN, _challenge("invalid_token", INVALID_TOKEN))


def capability_denied() -> JSONResponse:
    """403 when the token's grants do not cover the selected skill."""
    challenge = _challenge("insufficient_scope", CAPABILITY_DENIED)
    return _refusal(403, "insufficient_scope", CAPABILITY_DENIED, challenge)


def constraint_violation(violations: Sequence[ConstraintViolation]) -> JSONResponse:
    """403 when the arguments break the grant's constraints; more scope would not help."""
    fields = [{"field": v.field, "operator": v.operator} for v in violations]
    return _refusal(403, "access_denied", CONSTRAINT_VIOLATION, violations=fields)
