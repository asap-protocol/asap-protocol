"""Lock envelope sender checks to the authenticated agent id."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from asap.models.entities import AuthScheme, Capability, Endpoint, Manifest, Skill
from asap.transport.middleware import (
    ERROR_SENDER_MISMATCH,
    AuthenticationMiddleware,
    BearerTokenValidator,
)

_AGENT_ID = "urn:asap:agent:bot-01"
_NEIGHBORS = (
    "urn:asap:agent:bot-010",
    "urn:asap:agent:bot-0",
    "urn:asap:agent:bot",
    "URN:asap:agent:bot-01",
    " urn:asap:agent:bot-01",
    "urn:asap:agent:bot-01 ",
)


def _manifest() -> Manifest:
    return Manifest(
        id="urn:asap:agent:receiver",
        name="Receiver",
        version="1.0.0",
        description="Sender match fixture",
        capabilities=Capability(
            asap_version="0.1",
            skills=[Skill(id="echo", description="Echo")],
            state_persistence=False,
        ),
        endpoints=Endpoint(asap="http://localhost:8000/asap"),
        auth=AuthScheme(schemes=["bearer"]),
    )


def _accept_agent(_token: str) -> str:
    return _AGENT_ID


def test_sender_match_is_exact() -> None:
    """Prefix, case, and padding must not satisfy the authenticated sender."""
    middleware = AuthenticationMiddleware(_manifest(), BearerTokenValidator(_accept_agent))
    middleware.verify_sender_matches_auth(_AGENT_ID, _AGENT_ID)

    for sender in _NEIGHBORS:
        with pytest.raises(HTTPException) as exc_info:
            middleware.verify_sender_matches_auth(_AGENT_ID, sender)
        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == ERROR_SENDER_MISMATCH
