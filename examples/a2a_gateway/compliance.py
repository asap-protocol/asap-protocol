"""Compliance mode of the served gateway: test only, off by default.

Only the ``--compliance`` flag of ``serve.py`` turns it on; no environment
variable does. ``POST /compliance/probe-tokens`` mints a fresh set of access
tokens from the gateway's ``LocalIssuer`` on each call, in the JSON shape of
``examples/mcp_auth_http/src/compliance.ts``, so the ``ComplianceEndpointTokenProvider``
of ``asap-compliance`` reads it unchanged. Anyone who can reach the path can
call the skills: the served gateway binds a loopback address only and refuses
a non-local ``Host`` or ``Origin``.

The set also holds the public card key, because the demo makes a new key at
each start. The ``a2a-bridge`` profile never reads it: the operator of a demo
run copies it into the profile config. A real gateway has no such path.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from examples.a2a_gateway.demo_agent import A2A_PATH
from tests.auth.local_issuer import LocalIssuer

COMPLIANCE_PROBE_PATH = "/compliance/probe-tokens"
PROBE_SKILL_ID = "greet"
_ALLOWED_ARGUMENTS = {"name": "Ada", "language": "en"}
# "fr" is in the skill's input schema, but the grant's constraint allows "en" and "pt" only.
_CONSTRAINT_VIOLATION_ARGUMENTS = {"name": "Ada", "language": "fr"}
_OTHER_AUDIENCE_PATH = "/not-this-gateway"


def probe_token_set(
    issuer: LocalIssuer, origin: str, card_key: Mapping[str, Any]
) -> dict[str, Any]:
    """A fresh probe set for the gateway at *origin*; *card_key* is its ``kid`` and public JWK."""
    audience = f"{origin}{A2A_PATH}"

    def mint(token_audience: str, scope: str) -> str:
        return issuer.mint(issuer.claims(token_audience, scope=scope))

    return {
        "profile": "a2a-bridge",
        "test_only": True,
        "a2a_url": audience,
        "skill_id": PROBE_SKILL_ID,
        "allowed_arguments": dict(_ALLOWED_ARGUMENTS),
        "constraint_violation_arguments": dict(_CONSTRAINT_VIOLATION_ARGUMENTS),
        "external": {
            "valid": mint(audience, "demo:greet"),
            # A grant for another skill, so none for greet.
            "out_of_grant": mint(audience, "demo:echo"),
            "wrong_audience": mint(f"{origin}{_OTHER_AUDIENCE_PATH}", "demo:greet"),
        },
        "card_key": dict(card_key),
    }


def add_probe_token_route(
    app: FastAPI, issuer: LocalIssuer, origin: str, card_key: Mapping[str, Any]
) -> None:
    """Serve ``POST /compliance/probe-tokens`` on *app*; call it only in compliance mode."""

    async def probe_tokens() -> JSONResponse:
        body = probe_token_set(issuer, origin, card_key)
        return JSONResponse(body, headers={"Cache-Control": "no-store"})

    app.add_api_route(
        COMPLIANCE_PROBE_PATH, probe_tokens, methods=["POST"], include_in_schema=False
    )
