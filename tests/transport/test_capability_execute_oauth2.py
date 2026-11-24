"""``oauth2_config`` without the external-token config on capability execute.

Bug fixed in v2.6.0: ``OAuth2Middleware`` refused
every Agent JWT on ``POST /asap/capability/execute``, and an IdP token that
passed it then failed ``verify_agent_jwt``, so the route could never succeed.
Execute always requires and verifies its own bearer, so it owns that bearer
whenever ``oauth2_config`` is set, like ``/asap/agent/*``. ``list`` and
``describe`` answer without a bearer, so they stay behind the middleware.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from asap.auth.middleware import DEFAULT_CUSTOM_CLAIM
from tests.transport.test_capability_execute_unchanged import (
    _ASAP_CHALLENGE,
    EXPECTED_AGENT_PATH,
    execute_case,
)
from tests.transport.test_capability_external_oauth2 import _IDP, _server
from tests.transport.test_capability_external_tokens import _AUDIENCE, _App

if TYPE_CHECKING:
    from asap.models.entities import Manifest
    from asap.transport.rate_limit import ASAPRateLimiter


@pytest.fixture
def oauth2_only(sample_manifest: Manifest, isolated_rate_limiter: ASAPRateLimiter | None) -> _App:
    return _server(sample_manifest, isolated_rate_limiter)


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
@pytest.mark.parametrize("name", list(EXPECTED_AGENT_PATH))
async def test_agent_jwt_on_execute_is_verified_by_the_route(oauth2_only: _App, name: str) -> None:
    server = oauth2_only
    actual = await execute_case(
        server.client, server.app, server.agent_store, server.registry, name
    )
    assert actual == EXPECTED_AGENT_PATH[name]


def test_idp_token_on_execute_is_refused_by_the_agent_jwt_path(oauth2_only: _App) -> None:
    token = _IDP.mint(_IDP.claims(_AUDIENCE, scope="files:read"))
    response = oauth2_only.execute(token, {"capability": "file:read"})

    assert response.status_code == 401
    assert response.json()["detail"] == "invalid typ for agent JWT"
    assert response.headers["www-authenticate"] == _ASAP_CHALLENGE


@pytest.mark.parametrize(
    "path", ["/asap/capability/list", "/asap/capability/describe?name=file:read"]
)
def test_list_and_describe_still_need_an_idp_token(
    oauth2_only: _App, sample_manifest: Manifest, path: str
) -> None:
    idp_token = _IDP.mint(_IDP.claims(_AUDIENCE, **{DEFAULT_CUSTOM_CLAIM: sample_manifest.id}))

    anonymous = oauth2_only.client.get(path)
    with_idp_token = oauth2_only.client.get(path, headers={"Authorization": f"Bearer {idp_token}"})

    assert (anonymous.status_code, anonymous.json()) == (401, {"detail": "Authentication required"})
    assert with_idp_token.status_code == 200
