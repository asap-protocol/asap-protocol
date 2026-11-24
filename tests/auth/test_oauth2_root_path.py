"""OAuth2Middleware when the app runs under an ASGI ``root_path``.

A prefix reaches the app through ``Mount``, a server ``--root-path`` (uvicorn
builds ``path = root_path + target``) or ``FastAPI.root_path``. Routing matches on
the path without ``root_path``; the middleware must decide on that same path, so
a prefix neither skips token validation nor widens the ``/asap/agent/`` exemption.
"""

from __future__ import annotations

import base64
import time
from collections.abc import Callable
from typing import Any

import anyio
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from joserfc import jwk, jwt as jose_jwt
from starlette._utils import get_route_path
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.testclient import TestClient

from asap.auth import OAuth2Config
from asap.auth.agent_jwt import create_host_jwt
from asap.auth.middleware import DEFAULT_CUSTOM_CLAIM, _route_path
from asap.models.entities import Capability, Endpoint, Manifest, Skill
from asap.models.envelope import Envelope
from asap.models.payloads import TaskRequest
from asap.transport.jsonrpc import JsonRpcRequest
from asap.transport.server import create_app

MANIFEST_ID = "urn:asap:agent:test-server"


@pytest.fixture(scope="module")
def rsa_key() -> jwk.RSAKey:
    """One IdP signing key for the module (2048-bit generation is slow)."""
    return jwk.RSAKey.generate_key(2048, private=True)


def _app(key: jwk.RSAKey, *, required_scope: str | None = None) -> FastAPI:
    key_set = jwk.KeySet.import_key_set({"keys": [key.as_dict(private=False)]})

    async def fetch(_uri: str) -> jwk.KeySet:
        return key_set

    manifest = Manifest(
        id=MANIFEST_ID,
        name="Test Server",
        version="1.0.0",
        description="Test",
        capabilities=Capability(
            asap_version="0.1",
            skills=[Skill(id="echo", description="Echo")],
            state_persistence=False,
        ),
        endpoints=Endpoint(asap="http://localhost:8000/asap"),
    )
    return create_app(
        manifest,
        oauth2_config=OAuth2Config(
            jwks_uri="https://auth.example.com/jwks.json",
            required_scope=required_scope,
            jwks_fetcher=fetch,
        ),
        rate_limit="100000/minute",
        identity_rate_limit="100000/minute",
    )


def _idp_token(key: jwk.RSAKey, scope: str = "asap:execute") -> str:
    now = int(time.time())
    claims = {"sub": "urn:asap:agent:client", "scope": scope, "exp": now + 3600}
    claims[DEFAULT_CUSTOM_CLAIM] = MANIFEST_ID
    return jose_jwt.encode({"alg": "RS256", "typ": "JWT"}, claims, key)


def _asap_body() -> dict[str, Any]:
    envelope = Envelope(
        asap_version="0.1",
        sender="urn:asap:agent:client",
        recipient=MANIFEST_ID,
        payload_type="task.request",
        payload=TaskRequest(conversation_id="c1", skill_id="echo", input={"m": "hi"}).model_dump(),
    )
    params = {"envelope": envelope.model_dump(mode="json")}
    return JsonRpcRequest(method="asap.send", params=params, id="req-1").model_dump()


def _mount(app: FastAPI) -> tuple[TestClient, str]:
    parent = FastAPI()
    parent.mount("/api", app)
    return TestClient(parent), "/api"


def _server_root_path(app: FastAPI) -> tuple[TestClient, str]:
    # Same scope as ``uvicorn --root-path /api``: path keeps the prefix.
    return TestClient(app, root_path="/api"), "/api"


def _app_root_path(app: FastAPI) -> tuple[TestClient, str]:
    app.root_path = "/api"
    return TestClient(app), "/api"


def _nested_mount(app: FastAPI) -> tuple[TestClient, str]:
    outer = Starlette(routes=[Mount("/a", routes=[Mount("/b", app=app)])])
    return TestClient(outer), "/a/b"


SHAPES: dict[str, Callable[[FastAPI], tuple[TestClient, str]]] = {
    "mount": _mount,
    "server_root_path": _server_root_path,
    "app_root_path": _app_root_path,
    "nested_mount": _nested_mount,
}
PROTECTED = [("GET", "/asap/capability/list"), ("POST", "/asap")]


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize(("method", "path"), PROTECTED)
def test_prefixed_request_without_token_is_rejected(
    rsa_key: jwk.RSAKey, shape: str, method: str, path: str
) -> None:
    client, prefix = SHAPES[shape](_app(rsa_key))
    response = client.request(
        method, prefix + path, json=_asap_body() if method == "POST" else None
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required"}


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize(("method", "path"), PROTECTED)
def test_prefixed_request_with_valid_token_passes(
    rsa_key: jwk.RSAKey, shape: str, method: str, path: str
) -> None:
    client, prefix = SHAPES[shape](_app(rsa_key))
    response = client.request(
        method,
        prefix + path,
        json=_asap_body() if method == "POST" else None,
        headers={"Authorization": f"Bearer {_idp_token(rsa_key)}"},
    )
    assert response.status_code == 200
    if method == "POST":
        assert "result" in response.json()


def test_required_scope_applies_under_prefix(rsa_key: jwk.RSAKey) -> None:
    client, prefix = _mount(_app(rsa_key, required_scope="asap:execute"))
    response = client.post(
        prefix + "/asap",
        json=_asap_body(),
        headers={"Authorization": f"Bearer {_idp_token(rsa_key, scope='asap:read')}"},
    )
    assert response.status_code == 403


@pytest.mark.parametrize("url", ["/api/%61sap/capability/list", "/api%2Fasap/capability/list"])
def test_percent_encoded_prefixed_request_is_rejected(rsa_key: jwk.RSAKey, url: str) -> None:
    client, _ = _mount(_app(rsa_key))
    assert client.get(url).status_code == 401


@pytest.mark.parametrize("shape", SHAPES)
def test_agent_identity_route_stays_exempt_under_prefix(rsa_key: jwk.RSAKey, shape: str) -> None:
    """A Host JWT is not an IdP token; the route must still reach its own check."""
    raw = (
        Ed25519PrivateKey.generate()
        .public_key()
        .public_bytes(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
    )
    x = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    agent_jwk = {"kty": "OKP", "crv": "Ed25519", "x": x}
    host_token = create_host_jwt(
        Ed25519PrivateKey.generate(), aud=MANIFEST_ID, agent_public_key=agent_jwk, ttl_seconds=120
    )
    client, prefix = SHAPES[shape](_app(rsa_key))
    response = client.post(
        prefix + "/asap/agent/register", headers={"Authorization": f"Bearer {host_token}"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "pending"


# (scope path, scope root_path, status with no token). The path is decoded, as a
# server hands it over. 404 means the router matched nothing, so no handler ran.
SCOPES = [
    pytest.param("/asap/capability/list", "", 401, id="empty-root-path"),
    pytest.param("/asap/capability/list", "/api", 401, id="root-path-not-a-prefix"),
    pytest.param("/api//asap/capability/list", "/api/", 401, id="root-path-trailing-slash"),
    pytest.param("/api/asap/capability/list", "/api/", 404, id="trailing-slash-no-route"),
    pytest.param("/apix/asap/capability/list", "/api", 404, id="prefix-mid-segment"),
    pytest.param("/api//asap/capability/list", "/api", 404, id="double-slash"),
    pytest.param("/api/%61sap/capability/list", "/api", 404, id="percent-left-after-decoding"),
    pytest.param("/asap/agent/asap/capability/list", "/asap/agent", 401, id="exempt-like-prefix"),
    pytest.param("/api/asap/agent", "/api", 401, id="agent-without-slash"),
    pytest.param("/api/asap/agentx/register", "/api", 401, id="agent-lookalike"),
]


async def _status(app: FastAPI, path: str, root_path: str) -> int:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": root_path,
        "query_string": b"",
        "headers": [(b"host", b"testserver")],
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 50000),
    }
    sent: list[dict[str, Any]] = []
    done = anyio.Event()
    started = False

    async def receive() -> dict[str, Any]:
        nonlocal started
        if not started:
            started = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await done.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)
        if message["type"] == "http.response.body" and not message.get("more_body"):
            done.set()

    await app(scope, receive, send)
    return int(sent[0]["status"])


@pytest.mark.parametrize(("path", "root_path", "expected"), SCOPES)
async def test_hand_built_scope(
    rsa_key: jwk.RSAKey, path: str, root_path: str, expected: int
) -> None:
    assert await _status(_app(rsa_key), path, root_path) == expected


@pytest.mark.parametrize(("path", "root_path", "_expected"), SCOPES)
def test_route_path_matches_starlette_routing(path: str, root_path: str, _expected: int) -> None:
    scope = {"type": "http", "path": path, "root_path": root_path}
    assert _route_path(scope) == get_route_path(scope)
