"""HTTP helpers of the external-token path.

The route wiring is tested in ``tests/transport/test_capability_external_tokens.py``;
these tests pin which bearer takes which path, the responses for a refused
token, and the log events.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from unittest.mock import Mock

import httpx
import pytest
from fastapi.responses import JSONResponse

from asap.auth.capabilities import CapabilityGrant, check_capability_grant
from asap.auth.external_tokens import ExternalPrincipal, ExternalTokenConfig
from asap.auth.external_verifier import ExternalTokenVerifier
from asap.transport._external_auth import (
    is_agent_jwt,
    log_external_execute,
    verify_external_bearer,
)
from tests.auth.local_issuer import LocalIssuer

_AUDIENCE = "https://api.example/asap"
_GRANT = CapabilityGrant(
    capability="file.read", status="active", constraints={"path": {"in": ["/tmp"]}}
)


async def _public_resolver(hostname: str) -> list[str]:
    return ["93.184.216.34"]


@pytest.fixture
def idp() -> LocalIssuer:
    return LocalIssuer("https://idp.example")


def _verifier(idp: LocalIssuer, **entry: Any) -> ExternalTokenVerifier:
    config = ExternalTokenConfig.model_validate({"issuers": [idp.config_entry(_AUDIENCE, **entry)]})
    failing_idp = httpx.MockTransport(lambda request: httpx.Response(500))
    return ExternalTokenVerifier(config, resolver=_public_resolver, transport=failing_idp)


def _events(caplog: pytest.LogCaptureFixture, name: str) -> list[dict[str, Any]]:
    return [r.msg for r in caplog.records if isinstance(r.msg, dict) and r.msg["event"] == name]


@pytest.mark.parametrize(
    ("typ", "expected"),
    [("agent+jwt", True), ("Agent+JWT", False), ("host+jwt", False), ("at+jwt", False)],
)
def test_only_an_exact_agent_typ_takes_the_agent_path(
    idp: LocalIssuer, typ: str, expected: bool
) -> None:
    assert is_agent_jwt(idp.mint(idp.claims(_AUDIENCE), header={"typ": typ})) is expected


@pytest.mark.parametrize("token", ["opaque-token-value", "a.b.c", "\udcff.x.y", ""])
def test_a_bearer_that_is_not_a_jws_is_not_an_agent_jwt(token: str) -> None:
    assert is_agent_jwt(token) is False


async def test_parser_header_type_error_is_not_an_agent_jwt(
    idp: LocalIssuer, monkeypatch: pytest.MonkeyPatch
) -> None:
    parsed = Mock()
    parsed.headers.return_value = []
    parsed.payload = b"{}"
    monkeypatch.setattr("asap.auth.external_verifier.extract_compact", Mock(return_value=parsed))

    token = "local.test.signature"
    assert not is_agent_jwt(token)

    response = await verify_external_bearer(_verifier(idp), token, request_id="rid-1")
    assert isinstance(response, JSONResponse)
    assert response.status_code == 401


async def test_valid_token_gives_the_principal(idp: LocalIssuer) -> None:
    token = idp.mint(idp.claims(_AUDIENCE, subject="user-7"))
    principal = await verify_external_bearer(_verifier(idp), token, request_id="rid-1")
    assert isinstance(principal, ExternalPrincipal)
    assert principal.subject == "user-7"


@pytest.mark.parametrize(
    ("claims", "reason"),
    [
        ({"aud": "https://other.example"}, "audience_mismatch"),
        ({"iss": "https://stranger.example"}, "untrusted_issuer"),
        ({"sub": None}, "missing_claim"),
    ],
)
async def test_refused_token_is_one_401_and_a_logged_reason(
    idp: LocalIssuer, caplog: pytest.LogCaptureFixture, claims: dict[str, Any], reason: str
) -> None:
    token = idp.mint(idp.claims(_AUDIENCE, **claims))
    with caplog.at_level("INFO"):
        response = await verify_external_bearer(_verifier(idp), token, request_id="rid-2")

    assert isinstance(response, JSONResponse)
    assert response.status_code == 401
    assert json.loads(response.body) == {"detail": "invalid_token", "request_id": "rid-2"}
    assert response.headers["www-authenticate"] == 'Bearer error="invalid_token"'
    [event] = _events(caplog, "asap.capability.external_token_rejected")
    assert (event["reason"], event["request_id"]) == (reason, "rid-2")
    assert token not in caplog.text
    assert token.rsplit(".", 1)[-1] not in caplog.text


async def test_unreachable_key_source_is_503(
    idp: LocalIssuer, caplog: pytest.LogCaptureFixture
) -> None:
    verifier = _verifier(idp, key_source={"kind": "jwks_uri", "jwks_uri": f"{idp.issuer}/jwks"})
    token = idp.mint(idp.claims(_AUDIENCE))
    with caplog.at_level("INFO"):
        response = await verify_external_bearer(verifier, token, request_id="rid-3")

    assert isinstance(response, JSONResponse)
    assert response.status_code == 503
    assert json.loads(response.body) == {
        "detail": "Authentication service unavailable",
        "request_id": "rid-3",
    }
    assert "www-authenticate" not in response.headers
    [event] = _events(caplog, "asap.capability.external_token_rejected")
    assert event["reason"] == "key_source_unavailable"


async def test_a_forged_token_shows_a_listed_issuer_only_while_its_keys_are_down(
    idp: LocalIssuer,
) -> None:
    # The gap the module docstring names: kept so a
    # legitimate caller can tell a key-source outage from a bad token.
    verifier = _verifier(idp, key_source={"kind": "jwks_uri", "jwks_uri": f"{idp.issuer}/jwks"})
    forger, stranger = LocalIssuer(idp.issuer), LocalIssuer("https://stranger.example")
    statuses = []
    for signer in (forger, stranger):
        token = signer.mint(signer.claims(_AUDIENCE))
        response = await verify_external_bearer(verifier, token, request_id="rid-4")
        assert isinstance(response, JSONResponse)
        statuses.append(response.status_code)
    assert statuses == [503, 401]


@pytest.mark.parametrize(
    ("grant", "arguments", "outcome"),
    [
        (_GRANT, {"path": "/tmp"}, "executed"),
        (_GRANT, {"path": "/etc"}, "constraint_violated"),
        (None, {"path": "/tmp"}, "capability_not_granted"),
    ],
)
def test_execute_event_names_the_principal_and_the_outcome(
    caplog: pytest.LogCaptureFixture,
    grant: CapabilityGrant | None,
    arguments: dict[str, Any],
    outcome: str,
) -> None:
    principal = ExternalPrincipal(
        issuer="https://idp.example",
        subject="user-7",
        client_id="mcp-client",
        scopes=("files:read",),
        expires_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
        grants=(_GRANT,),
    )
    check = check_capability_grant(grant, arguments)
    with caplog.at_level("INFO"):
        log_external_execute(principal, "file.read", check, request_id="rid-4")

    [event] = _events(caplog, "asap.capability.external_execute")
    assert {key: event[key] for key in ("issuer", "subject", "client_id", "capability")} == {
        "issuer": "https://idp.example",
        "subject": "user-7",
        "client_id": "mcp-client",
        "capability": "file.read",
    }
    assert (event["outcome"], event["request_id"]) == (outcome, "rid-4")
