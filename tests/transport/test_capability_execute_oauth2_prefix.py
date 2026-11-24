"""The exempt execute route under an ASGI ``root_path`` (v2.6.0).

``OAuth2Middleware`` skips ``POST /asap/capability/execute``, whose route verifies
its own bearer, and decides on the path without ``root_path``. The two changes met
only in a rebase. Under no prefix and each shape of ``test_oauth2_root_path``,
execute stays exempt and its verifier still runs, no other path becomes exempt,
and ``required_scope`` and the identity binding still apply to every other path.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from starlette.testclient import TestClient

from asap.auth.middleware import DEFAULT_CUSTOM_CLAIM, ERROR_IDENTITY_MISMATCH
from tests.auth.test_oauth2_root_path import SHAPES as PREFIXED_SHAPES
from tests.auth.test_oauth2_root_path import _asap_body, _status
from tests.transport.test_capability_execute_unchanged import EXPECTED_AGENT_PATH, execute_case
from tests.transport.test_capability_external_oauth2 import (
    _IDP,
    _MIDDLEWARE_REFUSAL,
    _OTHER_AGENT,
    _PARTNER,
    _bound,
    _server,
    _token,
)
from tests.transport.test_capability_external_tokens import _AUDIENCE, _App
from tests.transport.test_capability_routes import _agent_jwt, _register_and_activate

if TYPE_CHECKING:
    from asap.models.entities import Manifest
    from asap.transport.rate_limit import ASAPRateLimiter


def _no_prefix(app: FastAPI) -> tuple[TestClient, str]:
    return TestClient(app), ""


SHAPES: dict[str, Callable[[FastAPI], tuple[TestClient, str]]] = {
    "no_prefix": _no_prefix,
    **PREFIXED_SHAPES,
}
CONFIGS = {"oauth2 only": (), "both configs": (_IDP, _PARTNER)}
_NO_TOKEN = (401, {"detail": "Authentication required"})
_NOT_FOUND = (404, {"detail": "Not Found"})
_FILE_READ = {"capability": "file:read"}


def _serve(server: _App, shape: str) -> _App:
    """Send ``server.client`` through ``shape``; a relative URL gets the shape's prefix."""
    client, prefix = SHAPES[shape](server.app)
    client.base_url = f"http://testserver{prefix}"
    server.client = client
    return server


async def _agent_minter(server: _App) -> Callable[[], str]:
    """Register an active agent granted ``file:read``; return a fresh-``jti`` Agent JWT minter."""
    host_sk, agent_sk = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    aid = await _register_and_activate(
        server.client, server.app, server.agent_store, host_sk, agent_sk
    )
    server.registry.grant(aid, "file:read")
    return lambda: _agent_jwt(agent_sk, host_sk, aid)


def _idp_token(scope: str, agent: str | None) -> str:
    """An ``oauth2_config`` IdP token; ``agent`` is the custom agent claim (``None``: absent)."""
    extra = {} if agent is None else {DEFAULT_CUSTOM_CLAIM: agent}
    return _IDP.mint(_IDP.claims(_AUDIENCE, scope=scope, **extra))


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
@pytest.mark.parametrize("name", list(EXPECTED_AGENT_PATH))
@pytest.mark.parametrize("config", CONFIGS)
@pytest.mark.parametrize("shape", SHAPES)
async def test_execute_is_exempt_and_verifies_its_own_agent_jwt(
    sample_manifest: Manifest,
    isolated_rate_limiter: ASAPRateLimiter | None,
    shape: str,
    config: str,
    name: str,
) -> None:
    # required_scope binds every other /asap path; an Agent JWT carries no scope.
    server = _server(
        sample_manifest, isolated_rate_limiter, *CONFIGS[config], required_scope="asap:execute"
    )
    server = _serve(server, shape)
    actual = await execute_case(
        server.client, server.app, server.agent_store, server.registry, name
    )
    expected = EXPECTED_AGENT_PATH[name]
    if shape != "no_prefix" and expected[2] is not None:
        # WWWAuthenticateASAPMiddleware matches request.url.path, which keeps the
        # prefix, so it adds no ASAP discovery challenge; the route's Bearer stays.
        expected = (*expected[:2], "Bearer")
    assert actual == expected


@pytest.mark.parametrize("shape", SHAPES)
def test_idp_token_on_execute_is_judged_by_the_route(
    sample_manifest: Manifest, isolated_rate_limiter: ASAPRateLimiter | None, shape: str
) -> None:
    oauth2_only = _serve(_server(sample_manifest, isolated_rate_limiter), shape)
    refused = oauth2_only.execute(_token(_IDP), _FILE_READ)
    assert (refused.status_code, refused.json()["detail"]) == (401, "invalid typ for agent JWT")

    both = _serve(_server(sample_manifest, isolated_rate_limiter, _IDP, _PARTNER), shape)
    for issuer in (_IDP, _PARTNER):
        accepted = both.execute(_token(issuer), _FILE_READ)
        assert accepted.status_code == 200, issuer.issuer
        assert accepted.json()["principal"]["issuer"] == issuer.issuer


@pytest.mark.parametrize("shape", SHAPES)
def test_required_claims_bind_the_idp_token_on_execute(
    sample_manifest: Manifest, isolated_rate_limiter: ASAPRateLimiter | None, shape: str
) -> None:
    server = _serve(_bound(sample_manifest, isolated_rate_limiter), shape)
    for agent, status in [(sample_manifest.id, 200), (_OTHER_AGENT, 401), (None, 401)]:
        response = server.execute(_idp_token("files:read", agent), _FILE_READ)
        assert response.status_code == status, agent


# Spelled path -> (no bearer, a valid Agent JWT). The Agent JWT is not an IdP
# token, so the middleware refuses it: a 200 means the execute route took it.
_SPELLINGS = {
    "/asap/capability/%65xecute": (_NO_TOKEN, 200),
    "/asap/capability%2Fexecute": (_NO_TOKEN, 200),
    "/asap/capability/execute/": (_NO_TOKEN, 401),
    "/asap/capability/execute%2F": (_NO_TOKEN, 401),
    "/asap/capability/executex": (_NO_TOKEN, 401),
    "/asap/capability/execute/x": (_NO_TOKEN, 401),
    "/asap/Capability/execute": (_NO_TOKEN, 401),
    "/asap/capability//execute": (_NO_TOKEN, 401),
    "/asap/x/asap/capability/execute": (_NO_TOKEN, 401),
    "/ASAP/capability/execute": (_NOT_FOUND, 404),
    "/x/asap/capability/execute": (_NOT_FOUND, 404),
}


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
@pytest.mark.parametrize("config", CONFIGS)
@pytest.mark.parametrize("shape", SHAPES)
async def test_near_miss_spellings_are_not_exempt(
    sample_manifest: Manifest,
    isolated_rate_limiter: ASAPRateLimiter | None,
    shape: str,
    config: str,
) -> None:
    server = _serve(_server(sample_manifest, isolated_rate_limiter, *CONFIGS[config]), shape)
    agent_jwt = await _agent_minter(server)
    for path, (anonymous, with_agent_jwt) in _SPELLINGS.items():
        response = server.client.post(path, json=_FILE_READ)
        assert (response.status_code, response.json()) == anonymous, path
        agent = {"Authorization": f"Bearer {agent_jwt()}"}
        response = server.client.post(path, json=_FILE_READ, headers=agent)
        assert response.status_code == with_agent_jwt, path
        if with_agent_jwt == 401:
            assert response.json() == _MIDDLEWARE_REFUSAL[1], path


_OTHER_PATHS = [
    ("GET", "/asap/capability/list"),
    ("GET", "/asap/capability/describe?name=file:read"),
    ("GET", "/asap/metrics"),
    ("POST", "/asap"),
]


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
@pytest.mark.parametrize(("method", "path"), _OTHER_PATHS)
@pytest.mark.parametrize("config", CONFIGS)
@pytest.mark.parametrize("shape", SHAPES)
async def test_other_asap_paths_keep_the_middleware_checks(
    sample_manifest: Manifest,
    isolated_rate_limiter: ASAPRateLimiter | None,
    shape: str,
    config: str,
    method: str,
    path: str,
) -> None:
    server = _server(
        sample_manifest, isolated_rate_limiter, *CONFIGS[config], required_scope="asap:execute"
    )
    server = _serve(server, shape)
    me = sample_manifest.id
    mismatch = (403, {"detail": f"{ERROR_IDENTITY_MISMATCH} (expected: {me})"})
    cases: list[tuple[str | None, tuple[int, dict[str, str]] | int]] = [
        (None, _NO_TOKEN),
        ((await _agent_minter(server))(), _MIDDLEWARE_REFUSAL),
        (_token(_PARTNER), _MIDDLEWARE_REFUSAL),
        (_idp_token("asap:read", me), (403, {"detail": "Insufficient scope"})),
        (_idp_token("asap:execute", _OTHER_AGENT), mismatch),
        (_idp_token("asap:execute", None), mismatch),
        (_idp_token("asap:execute", me), 200),
    ]
    for index, (token, expected) in enumerate(cases):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        body = _asap_body() if method == "POST" else None
        response = server.client.request(method, path, headers=headers, json=body)
        if isinstance(expected, int):
            assert response.status_code == expected, index
        else:
            assert (response.status_code, response.json()) == expected, index


# (scope path, scope root_path, status of a GET with no token), as in
# test_oauth2_root_path.SCOPES. Execute takes only POST, so 405 means the
# middleware let the request through and the router matched execute; no handler ran.
_EXECUTE_SCOPES = [
    pytest.param("/asap/capability/execute", "", 405, id="no-prefix"),
    pytest.param("/api/asap/capability/execute", "/api", 405, id="prefixed"),
    pytest.param("/asap/capability/execute", "/api", 405, id="root-path-not-a-prefix"),
    pytest.param("/api//asap/capability/execute", "/api/", 405, id="root-path-trailing-slash"),
    pytest.param("/api/asap/capability/execute", "/api/", 404, id="trailing-slash-no-route"),
    pytest.param("/apix/asap/capability/execute", "/api", 404, id="prefix-mid-segment"),
    pytest.param("/api//asap/capability/execute", "/api", 404, id="double-slash"),
    pytest.param("/api/asap/capability/%65xecute", "/api", 401, id="percent-left-after-decoding"),
    pytest.param("/api/x/asap/capability/execute", "/api", 404, id="ends-with-exempt-path"),
    pytest.param("/asap/capability/execute", "/asap/capability", 404, id="exempt-like-prefix"),
    pytest.param(
        "/asap/capability/execute", "/asap/capability/execute", 404, id="prefix-is-exempt"
    ),
    pytest.param("/asap/capability/list", "/asap/capability", 404, id="list-under-exempt-like"),
    pytest.param(
        "/asap/capability/asap/capability/execute",
        "/asap/capability",
        405,
        id="exempt-under-lookalike",
    ),
    pytest.param(
        "/asap/capability/asap/capability/list", "/asap/capability", 401, id="list-under-lookalike"
    ),
]


@pytest.mark.parametrize(("path", "root_path", "expected"), _EXECUTE_SCOPES)
@pytest.mark.parametrize("config", CONFIGS)
async def test_hand_built_scope_on_execute(
    sample_manifest: Manifest,
    isolated_rate_limiter: ASAPRateLimiter | None,
    config: str,
    path: str,
    root_path: str,
    expected: int,
) -> None:
    server = _server(sample_manifest, isolated_rate_limiter, *CONFIGS[config])
    assert await _status(server.app, path, root_path) == expected
