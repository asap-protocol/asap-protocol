"""External access tokens on ``POST /asap/capability/execute`` (COMPAT-001/002).

Grants come only from the issuer's rules, and the Agent JWT path answers
exactly as before.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from asap.auth.capabilities import CapabilityRegistry
from asap.auth.external_tokens import ExternalTokenConfig
from asap.auth.identity import InMemoryAgentStore, InMemoryHostStore
from asap.transport.server import create_app
from tests.auth.local_issuer import LocalIssuer, nested_payload_token, unsigned_token
from tests.auth.test_external_introspection import _TOKEN as _OPAQUE_TOKEN
from tests.auth.test_external_introspection import _FakeIntrospectionEndpoint
from tests.transport.test_capability_execute_unchanged import (
    _ASAP_CHALLENGE,
    EXPECTED_AGENT_PATH,
    EXPECTED_NON_AGENT_BEARER,
    execute_case,
)
from tests.transport.test_capability_routes import _DEFAULT_CAPS

if TYPE_CHECKING:
    from asap.models.entities import Manifest
    from asap.transport.rate_limit import ASAPRateLimiter

_AUDIENCE = "https://api.example/asap"
_RULES = [
    {"scope": "files:read", "capability": "file:read"},
    {"scope": "files:write", "capability": "file:write", "constraints": {"path": {"in": ["/tmp"]}}},
]
_EXECUTE = "/asap/capability/execute"
_INVALID_TOKEN = {"detail": "invalid_token", "request_id": "<request_id>"}
_INVALID_TOKEN_CHALLENGE = f'Bearer error="invalid_token", {_ASAP_CHALLENGE}'


class _App:
    """An app whose external-token config lists ``issuers`` (none: no config), with its stores.

    ``entry_overrides`` adds or replaces fields of every issuer entry.
    """

    def __init__(
        self,
        manifest: Manifest,
        limiter: ASAPRateLimiter | None,
        *issuers: LocalIssuer,
        entry_overrides: dict[str, Any] | None = None,
        **create_app_kwargs: Any,
    ) -> None:
        self.agent_store = InMemoryAgentStore()
        overrides = entry_overrides or {}
        entries = [
            issuer.config_entry(_AUDIENCE, grant_rules=_RULES, **overrides) for issuer in issuers
        ]
        config = ExternalTokenConfig.model_validate({"issuers": entries}) if entries else None
        self.app: FastAPI = create_app(
            manifest,
            rate_limit="999999/minute",
            identity_host_store=InMemoryHostStore(agent_store=self.agent_store),
            identity_agent_store=self.agent_store,
            identity_rate_limit="999999/minute",
            external_token_config=config,
            **create_app_kwargs,
        )
        self.registry = CapabilityRegistry()
        for capability in _DEFAULT_CAPS:
            self.registry.register(capability)
        self.app.state.capability_registry = self.registry
        if limiter is not None:
            self.app.state.limiter = limiter
        self.client = TestClient(self.app)

    def execute(self, token: str | None, body: dict[str, Any]) -> httpx.Response:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return self.client.post(_EXECUTE, headers=headers, json=body)


@pytest.fixture
def idp() -> LocalIssuer:
    return LocalIssuer("https://idp.example")


@pytest.fixture
def server(
    sample_manifest: Manifest, isolated_rate_limiter: ASAPRateLimiter | None, idp: LocalIssuer
) -> _App:
    return _App(sample_manifest, isolated_rate_limiter, idp)


def test_allowed_call_succeeds_with_the_principal_in_the_body(
    server: _App, idp: LocalIssuer, caplog: pytest.LogCaptureFixture
) -> None:
    token = idp.mint(idp.claims(_AUDIENCE, subject="user-42", scope="files:read"))
    with caplog.at_level("INFO"):
        response = server.execute(token, {"capability": "file:read"})

    assert response.status_code == 200
    assert response.json() == {
        "capability": "file:read",
        "status": "executed",
        "principal": {"type": "external", "issuer": idp.issuer, "subject": "user-42"},
    }
    [event] = [
        r.msg for r in caplog.records
        if isinstance(r.msg, dict) and r.msg["event"] == "asap.capability.external_execute"
    ]  # fmt: skip
    assert (event["subject"], event["outcome"]) == ("user-42", "executed")
    assert token not in caplog.text


def test_unmapped_capability_is_denied_with_insufficient_scope(
    server: _App, idp: LocalIssuer
) -> None:
    token = idp.mint(idp.claims(_AUDIENCE, scope="files:read admin"))
    response = server.execute(token, {"capability": "admin:config"})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "capability_not_granted"
    assert response.json()["error"]["data"] == {"required_capability": "admin:config"}
    assert response.headers["www-authenticate"] == 'Bearer error="insufficient_scope"'


@pytest.mark.parametrize("arguments", [{"path": "/etc"}, None])
def test_constraint_violation_is_denied_without_a_challenge(
    server: _App, idp: LocalIssuer, arguments: dict[str, Any] | None
) -> None:
    token = idp.mint(idp.claims(_AUDIENCE, scope="files:write"))
    response = server.execute(token, {"capability": "file:write", "arguments": arguments})

    assert response.status_code == 403
    assert response.json()["error"] == "constraint_violated"
    assert [v["field"] for v in response.json()["violations"]] == ["path"]
    assert "www-authenticate" not in response.headers


def _rejected_tokens(idp: LocalIssuer) -> dict[str, str]:
    stranger = LocalIssuer("https://stranger.example")
    return {
        "unknown issuer": stranger.mint(stranger.claims(_AUDIENCE, scope="files:read")),
        "wrong audience": idp.mint(idp.claims("https://other.example", scope="files:read")),
        "expired": idp.mint(idp.claims(_AUDIENCE, scope="files:read", lifetime_seconds=-600)),
        "host+jwt": idp.mint(idp.claims(_AUDIENCE), header={"typ": "host+jwt"}),
        "alg none": unsigned_token({"alg": "none"}, idp.claims(_AUDIENCE, scope="files:read")),
        # Both used to be unhandled 500s.
        "alg list": unsigned_token({"alg": ["ES256"]}, idp.claims(_AUDIENCE, scope="files:read")),
        "payload nested too deeply": nested_payload_token({"alg": "ES256", "kid": idp.kid}),
    }


def test_rejected_tokens_get_one_body_that_names_no_issuer(
    server: _App, idp: LocalIssuer, caplog: pytest.LogCaptureFixture
) -> None:
    tokens = _rejected_tokens(idp)
    with caplog.at_level("INFO"):
        responses = {
            name: server.execute(t, {"capability": "file:read"}) for name, t in tokens.items()
        }

    for name, response in responses.items():
        body = {**response.json(), "request_id": "<request_id>"}
        assert (response.status_code, body) == (401, _INVALID_TOKEN), name
        assert response.headers["www-authenticate"] == _INVALID_TOKEN_CHALLENGE
    for token in tokens.values():
        assert token not in caplog.text
        assert all(token not in r.text for r in responses.values())


def test_revoked_opaque_token_gets_401_once_the_cache_ttl_passes(
    sample_manifest: Manifest,
    isolated_rate_limiter: ASAPRateLimiter | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = _FakeIntrospectionEndpoint()
    server = _App(sample_manifest, isolated_rate_limiter)
    # create_app builds a verifier that fetches over the network; this one uses fakes.
    server.app.state.external_token_verifier = endpoint.verifier(cache_ttl_seconds=5)
    clock = [time.time()]
    monkeypatch.setattr("asap.auth.introspection.time.time", lambda: clock[0])
    assert server.execute(_OPAQUE_TOKEN, {"capability": "file.read"}).status_code == 200

    endpoint.answer = {"active": False}
    clock[0] += 6
    response = server.execute(_OPAQUE_TOKEN, {"capability": "file.read"})
    assert (response.status_code, response.json()["detail"]) == (401, "invalid_token")


def test_token_is_checked_before_the_body(server: _App) -> None:
    response = server.client.post(
        _EXECUTE, headers={"Authorization": "Bearer opaque"}, content=b"not json"
    )
    assert response.status_code == 401


def test_bad_body_after_a_valid_token_is_400(server: _App, idp: LocalIssuer) -> None:
    token = idp.mint(idp.claims(_AUDIENCE, scope="files:read"))
    response = server.client.post(
        _EXECUTE, headers={"Authorization": f"Bearer {token}"}, content=b"not json"
    )
    assert response.status_code == 400


def test_external_token_with_agent_typ_goes_to_the_agent_path_and_fails(
    server: _App, idp: LocalIssuer
) -> None:
    token = idp.mint(idp.claims(_AUDIENCE, scope="files:read"), header={"typ": "agent+jwt"})
    response = server.execute(token, {"capability": "file:read"})
    assert (response.status_code, response.json()["detail"]) == (401, "unknown agent")


def test_capability_list_ignores_an_external_token(server: _App, idp: LocalIssuer) -> None:
    token = idp.mint(idp.claims(_AUDIENCE, scope="files:read"))
    response = server.client.get(
        "/asap/capability/list", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    assert all("grant_status" not in item for item in response.json()["capabilities"])


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
@pytest.mark.parametrize("name", list(EXPECTED_AGENT_PATH))
async def test_agent_jwt_path_answers_as_before_with_the_config_set(
    server: _App, name: str
) -> None:
    actual = await execute_case(
        server.client, server.app, server.agent_store, server.registry, name
    )
    assert actual == EXPECTED_AGENT_PATH[name]


@pytest.mark.parametrize("name", list(EXPECTED_NON_AGENT_BEARER))
async def test_other_bearers_take_the_external_path_with_the_config_set(
    server: _App, name: str
) -> None:
    actual = await execute_case(
        server.client, server.app, server.agent_store, server.registry, name
    )
    assert actual == (401, _INVALID_TOKEN, _INVALID_TOKEN_CHALLENGE)
