"""WebSocket auth enforcement under OAuth2-only deployments (B4/BUG #4).

In an OAuth2-only deployment (``oauth2_config`` set, ``manifest.auth`` left
``None``) the HTTP path ``/asap`` is protected by ``OAuth2Middleware`` while
the WebSocket path ``/asap/ws`` historically bypassed the middleware stack
entirely: ``handle_websocket_connection`` synthesizes a fake ``Request`` and
dispatches messages straight to the handler, so a client could send
``task.request`` envelopes with no Bearer token at all. These tests pin the
fail-closed behaviour: a WS connection without a valid Bearer JWT is rejected
at acceptance time, while a valid JWT is still admitted.
"""

from __future__ import annotations

import json
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient
from fastapi.websockets import WebSocketDisconnect
from joserfc import jwk, jwt as jose_jwt

from asap.auth import OAuth2Config
from asap.auth.middleware import DEFAULT_CUSTOM_CLAIM
from asap.models.entities import Capability, Endpoint, Manifest, Skill
from asap.models.envelope import Envelope
from asap.models.payloads import TaskRequest
from asap.transport.handlers import create_default_registry
from asap.transport.jsonrpc import ASAP_METHOD, JsonRpcRequest
from asap.transport.server import create_app
from asap.transport.websocket import WS_CLOSE_AUTH_REQUIRED

from .conftest import TEST_RATE_LIMIT_DEFAULT


def _oauth2_only_manifest() -> Manifest:
    """Manifest with no ``auth`` block — OAuth2 is the sole auth path."""
    return Manifest(
        id="urn:asap:agent:test-server",
        name="Test Server",
        version="1.0.0",
        description="OAuth2-only test server",
        capabilities=Capability(
            asap_version="0.1",
            skills=[Skill(id="echo", description="Echo")],
            state_persistence=False,
        ),
        endpoints=Endpoint(asap="http://localhost:8000/asap"),
    )


def _task_request_body(req_id: str | int = "ws-oauth2-1") -> str:
    """Serialize a minimal ``task.request`` JSON-RPC frame."""
    envelope = Envelope(
        asap_version="0.1",
        sender="urn:asap:agent:client",
        recipient="urn:asap:agent:test-server",
        payload_type="task.request",
        payload=TaskRequest(
            conversation_id="conv-ws-oauth2",
            skill_id="echo",
            input={"message": "hi"},
        ).model_dump(),
    )
    rpc = JsonRpcRequest(
        method=ASAP_METHOD,
        params={"envelope": envelope.model_dump(mode="json")},
        id=req_id,
    )
    return json.dumps(rpc.model_dump())


def _make_oauth2_app(
    key_set: jwk.KeySet,
    *,
    required_scope: str | None = None,
) -> Any:
    """Build an OAuth2-only app that resolves JWKS from ``key_set``."""

    async def mock_jwks(_uri: str) -> jwk.KeySet:
        return key_set

    return create_app(
        _oauth2_only_manifest(),
        registry=create_default_registry(),
        oauth2_config=OAuth2Config(
            jwks_uri="https://auth.example.com/jwks.json",
            path_prefix="/asap",
            required_scope=required_scope,
            jwks_fetcher=mock_jwks,
        ),
        rate_limit=TEST_RATE_LIMIT_DEFAULT,
    )


def _mint_valid_jwt(signing_key: jwk.RSAKey, *, scope: str = "asap:execute") -> str:
    """Mint a valid RS256 JWT carrying the manifest-bound custom claim."""
    now = int(time.time())
    return jose_jwt.encode(
        {"alg": "RS256", "typ": "JWT"},
        {
            "sub": "urn:asap:agent:client",
            "scope": scope,
            "exp": now + 3600,
            DEFAULT_CUSTOM_CLAIM: "urn:asap:agent:test-server",
        },
        signing_key,
    )


class TestWebSocketOAuth2Enforcement:
    """WS connections under OAuth2-only deployments must be authenticated."""

    def test_ws_without_bearer_is_rejected(self) -> None:
        """No Bearer token → connection is closed, message not dispatched.

        This is the core regression for B4/BUG #4: previously the envelope
        reached the handler and returned a ``task.response`` even though the
        HTTP path would have returned 401.
        """
        key = jwk.RSAKey.generate_key(2048, private=True)
        key_set = jwk.KeySet.import_key_set({"keys": [key.as_dict(private=False)]})
        app = _make_oauth2_app(key_set)

        from fastapi.websockets import WebSocketDisconnect

        with TestClient(app) as client, client.websocket_connect("/asap/ws") as websocket:
            websocket.send_text(_task_request_body())
            with pytest.raises(WebSocketDisconnect) as exc_info:
                websocket.receive_text()
            # B4 closes with WS_CLOSE_AUTH_REQUIRED (4401). Assert it specifically so a
            # regression to a rate-limit close (1008) or a silent dispatch is caught
            # (review follow-up: the loose {4401, 1008, None} set hid such regressions).
            assert exc_info.value.code == WS_CLOSE_AUTH_REQUIRED

    def test_ws_with_valid_bearer_is_admitted(self) -> None:
        """A valid Bearer JWT lets the connection through and dispatches the envelope."""
        key = jwk.RSAKey.generate_key(2048, private=True)
        key_set = jwk.KeySet.import_key_set({"keys": [key.as_dict(private=False)]})
        app = _make_oauth2_app(key_set)
        token = _mint_valid_jwt(key)

        with (
            TestClient(app) as client,
            client.websocket_connect(
                "/asap/ws", headers={"Authorization": f"Bearer {token}"}
            ) as websocket,
        ):
            websocket.send_text(_task_request_body())
            response_text = websocket.receive_text()

        data = json.loads(response_text)
        assert data.get("jsonrpc") == "2.0"
        assert "result" in data
        assert data["result"]["envelope"].get("payload_type") == "task.response"

    def test_ws_with_invalid_bearer_is_rejected(self) -> None:
        """A malformed/garbage Bearer token is rejected just like a missing one."""
        key = jwk.RSAKey.generate_key(2048, private=True)
        key_set = jwk.KeySet.import_key_set({"keys": [key.as_dict(private=False)]})
        app = _make_oauth2_app(key_set)

        from fastapi.websockets import WebSocketDisconnect

        with (
            TestClient(app) as client,
            client.websocket_connect(
                "/asap/ws", headers={"Authorization": "Bearer not-a-jwt"}
            ) as websocket,
        ):
            websocket.send_text(_task_request_body())
            with pytest.raises(WebSocketDisconnect) as exc_info:
                websocket.receive_text()
            # B4 closes with WS_CLOSE_AUTH_REQUIRED (4401) for any auth failure
            # (missing OR invalid token). Assert the specific code so a regression
            # to a silent dispatch or a different close code is caught.
            assert exc_info.value.code == WS_CLOSE_AUTH_REQUIRED

    def test_ws_rejects_token_missing_required_scope(self) -> None:
        """A valid JWT without ``required_scope`` is closed and not dispatched."""
        key = jwk.RSAKey.generate_key(2048, private=True)
        key_set = jwk.KeySet.import_key_set({"keys": [key.as_dict(private=False)]})
        app = _make_oauth2_app(key_set, required_scope="asap:admin")
        token = _mint_valid_jwt(key, scope="asap:execute")

        with (
            TestClient(app) as client,
            client.websocket_connect(
                "/asap/ws", headers={"Authorization": f"Bearer {token}"}
            ) as websocket,
        ):
            websocket.send_text(_task_request_body())
            with pytest.raises(WebSocketDisconnect) as exc_info:
                websocket.receive_text()
            assert exc_info.value.code == WS_CLOSE_AUTH_REQUIRED

    def test_ws_admits_token_that_includes_required_scope(self) -> None:
        """A JWT that includes ``required_scope`` is admitted and dispatched."""
        key = jwk.RSAKey.generate_key(2048, private=True)
        key_set = jwk.KeySet.import_key_set({"keys": [key.as_dict(private=False)]})
        app = _make_oauth2_app(key_set, required_scope="asap:admin")
        token = _mint_valid_jwt(key, scope="asap:execute asap:admin")

        with (
            TestClient(app) as client,
            client.websocket_connect(
                "/asap/ws", headers={"Authorization": f"Bearer {token}"}
            ) as websocket,
        ):
            websocket.send_text(_task_request_body())
            response_text = websocket.receive_text()

        data = json.loads(response_text)
        assert data.get("jsonrpc") == "2.0"
        assert data["result"]["envelope"].get("payload_type") == "task.response"
