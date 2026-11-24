"""``oauth2_config`` and the external-token config together.

With both set, ``POST /asap/capability/execute`` owns its bearer: the
middleware skips that exact path, and the route verifies the token once,
on the Agent JWT path or the external path. Every other ``/asap`` path stays
behind the middleware, with or without the external config. To accept the
``oauth2_config`` IdP on execute, list it as an external issuer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from joserfc import jwk

from asap.auth import OAuth2Config
from tests.auth.local_issuer import LocalIssuer
from tests.transport.test_capability_execute_unchanged import EXPECTED_AGENT_PATH, execute_case
from tests.transport.test_capability_external_tokens import _AUDIENCE, _App

if TYPE_CHECKING:
    from asap.models.entities import Manifest
    from asap.transport.rate_limit import ASAPRateLimiter

_IDP = LocalIssuer("https://idp.example")
_PARTNER = LocalIssuer("https://partner.example")
_MIDDLEWARE_REFUSAL = (401, {"detail": "Invalid authentication token"})


async def _idp_keys(uri: str) -> jwk.KeySet:
    return jwk.KeySet.import_key_set(_IDP.jwks)


def _server(manifest: Manifest, limiter: ASAPRateLimiter | None, *issuers: LocalIssuer) -> _App:
    oauth2 = OAuth2Config(jwks_uri="https://idp.example/jwks", jwks_fetcher=_idp_keys)
    return _App(manifest, limiter, *issuers, oauth2_config=oauth2)


@pytest.fixture
def both(sample_manifest: Manifest, isolated_rate_limiter: ASAPRateLimiter | None) -> _App:
    return _server(sample_manifest, isolated_rate_limiter, _IDP, _PARTNER)


def _token(issuer: LocalIssuer) -> str:
    return issuer.mint(issuer.claims(_AUDIENCE, scope="files:read"))


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
@pytest.mark.parametrize("name", list(EXPECTED_AGENT_PATH))
async def test_agent_jwt_on_execute_is_verified_by_the_route(both: _App, name: str) -> None:
    actual = await execute_case(both.client, both.app, both.agent_store, both.registry, name)
    assert actual == EXPECTED_AGENT_PATH[name]


@pytest.mark.parametrize("issuer", [_IDP, _PARTNER], ids=["oauth2 idp", "partner"])
def test_listed_issuers_execute_through_the_route(both: _App, issuer: LocalIssuer) -> None:
    response = both.execute(_token(issuer), {"capability": "file:read"})
    assert response.status_code == 200
    assert response.json()["principal"]["issuer"] == issuer.issuer


def test_idp_token_not_listed_is_refused_by_the_route(
    sample_manifest: Manifest, isolated_rate_limiter: ASAPRateLimiter | None
) -> None:
    server = _server(sample_manifest, isolated_rate_limiter, _PARTNER)
    response = server.execute(_token(_IDP), {"capability": "file:read"})
    assert (response.status_code, response.json()["detail"]) == (401, "invalid_token")


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/asap/capability/list"),
        ("POST", "/asap/capability/execute/"),
        ("POST", "/asap/capability//execute"),
        ("POST", "/asap"),
    ],
)
@pytest.mark.parametrize("external_config", [True, False], ids=["both configs", "oauth2 only"])
def test_other_asap_paths_stay_behind_the_middleware(
    sample_manifest: Manifest,
    isolated_rate_limiter: ASAPRateLimiter | None,
    external_config: bool,
    method: str,
    path: str,
) -> None:
    issuers = (_IDP, _PARTNER) if external_config else ()
    server = _server(sample_manifest, isolated_rate_limiter, *issuers)
    for token, expected in [
        (_token(_PARTNER), _MIDDLEWARE_REFUSAL),
        (None, (401, {"detail": "Authentication required"})),
    ]:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        response = server.client.request(method, path, headers=headers, json={})
        assert (response.status_code, response.json()) == expected, (path, token is None)
