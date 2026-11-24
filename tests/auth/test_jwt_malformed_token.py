"""A malformed Agent, Host, or delegation JWT is refused, never raised.

These verifiers parse the token before they know a key, so a caller without
credentials controls the whole header and payload. Each input below used to
raise: the identity routes answered 500 and the MCP Auth Bridge an internal
error, where other malformed tokens get 401 or ``asap:invalid_token``.
"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from joserfc import jwt as jose_jwt
from joserfc.jwk import OKPKey

from asap.auth.agent_jwt import JwtVerifyResult, verify_agent_jwt, verify_host_jwt
from asap.auth.capabilities import CapabilityRegistry
from asap.auth.identity import InMemoryAgentStore, InMemoryHostStore, jwk_thumbprint_sha256
from asap.economics.delegation import get_jti_from_jwt, validate_delegation
from asap.mcp.auth import MCPAuthConfig, protect_server
from asap.mcp.auth.errors import INVALID_TOKEN
from asap.mcp.server import MCPServer
from asap.transport.server import create_app
from tests.adapters.mcp.helpers import error_text, tool_call_params

if TYPE_CHECKING:
    from asap.models.entities import Manifest
    from asap.transport.rate_limit import ASAPRateLimiter

# CPython 3.13 raises RecursionError near 10,000 levels. joserfc refuses a
# payload segment over 128,000 bytes; 20,000 levels encode to about 53,000.
_DEPTH = 20_000
_NESTED = b"[" * _DEPTH + b"]" * _DEPTH
_AUD = "urn:asap:agent:test-server"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _token(typ: str, payload: bytes, *, header: bytes | None = None) -> str:
    """Compact token from raw segments; the signature is never reached."""
    raw_header = header or json.dumps({"alg": "EdDSA", "typ": typ}).encode()
    return f"{_b64url(raw_header)}.{_b64url(payload)}.{_b64url(bytes(64))}"


def _self_signed_host_jwt(**claims: Any) -> str:
    """Host JWT signed by the key it carries, as any caller can mint one."""
    key = OKPKey.generate_key("Ed25519")
    public = dict(key.as_dict(private=False))
    now = int(time.time())
    base = {"iss": jwk_thumbprint_sha256(public), "aud": _AUD, "iat": now, "exp": now + 60}
    payload = {**base, "jti": "jti-1", "host_public_key": public, **claims}
    return jose_jwt.encode({"alg": "EdDSA", "typ": "host+jwt"}, payload, key, algorithms=["EdDSA"])


async def _verify_agent(token: str) -> JwtVerifyResult:
    return await verify_agent_jwt(token, InMemoryHostStore(), InMemoryAgentStore())


async def _verify_host(token: str) -> JwtVerifyResult:
    return await verify_host_jwt(token, InMemoryHostStore(), expected_audience=_AUD)


_Verifier = Callable[[str], Awaitable[JwtVerifyResult]]
_VERIFIERS = [
    pytest.param("agent+jwt", _verify_agent, id="agent"),
    pytest.param("host+jwt", _verify_host, id="host"),
]
# A JSON payload that is not an object, and a header joserfc indexes with "b64".
_NOT_OBJECT_PAYLOADS = [_NESTED, b"[1]", b'"sub"', b"5", b"null"]
_NOT_OBJECT_HEADERS = [b'["alg", "b64"]', b'"alg b64"']
_UNKNOWN_CURVE = json.dumps({"host_public_key": {"kty": "OKP", "crv": "P-256", "x": "AA"}})


@pytest.mark.parametrize(("typ", "verify"), _VERIFIERS)
@pytest.mark.parametrize(
    "payload", _NOT_OBJECT_PAYLOADS, ids=["nested", "array", "str", "int", "null"]
)
async def test_payload_not_an_object_is_invalid_structure(
    typ: str, verify: _Verifier, payload: bytes
) -> None:
    result = await verify(_token(typ, payload))

    assert result.ok is False
    assert result.error is not None and result.error.startswith("invalid JWT structure: ")


@pytest.mark.parametrize(("typ", "verify"), _VERIFIERS)
@pytest.mark.parametrize("header", _NOT_OBJECT_HEADERS, ids=["array", "str"])
async def test_header_not_an_object_is_invalid_structure(
    typ: str, verify: _Verifier, header: bytes
) -> None:
    result = await verify(_token(typ, b'{"sub": "a"}', header=header))

    assert result.ok is False
    assert result.error is not None and result.error.startswith("invalid JWT structure: ")


@pytest.mark.parametrize(
    ("token", "error"),
    [
        (_token("host+jwt", _UNKNOWN_CURVE.encode()), "invalid host JWT: "),
        (_self_signed_host_jwt(exp=10**400), "token expired or missing exp"),
        (_self_signed_host_jwt(exp=-(10**400)), "token expired or missing exp"),
        (_self_signed_host_jwt(iat=10**400), "invalid iat (too far in the future)"),
    ],
    ids=["unknown-curve", "exp-overflow", "exp-underflow", "iat-overflow"],
)
async def test_host_jwt_claim_out_of_range_is_refused(token: str, error: str) -> None:
    result = await _verify_host(token)

    assert result.ok is False
    assert result.error is not None and result.error.startswith(error)


def test_delegation_nested_payload_is_malformed() -> None:
    token = _token("JWT", _NESTED)

    result = validate_delegation(
        token,
        "task.execute",
        public_key_resolver=lambda _iss: Ed25519PrivateKey.generate().public_key(),
    )

    assert (result.success, result.error) == (False, "Invalid or malformed token")
    assert get_jti_from_jwt(token) is None


@pytest.fixture
def identity_client(
    sample_manifest: Manifest, isolated_rate_limiter: ASAPRateLimiter | None
) -> TestClient:
    agent_store = InMemoryAgentStore()
    app = create_app(
        sample_manifest,
        rate_limit="999999/minute",
        identity_host_store=InMemoryHostStore(agent_store=agent_store),
        identity_agent_store=agent_store,
        identity_rate_limit="999999/minute",
    )
    app.state.capability_registry = CapabilityRegistry()
    if isolated_rate_limiter is not None:
        app.state.limiter = isolated_rate_limiter
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    ("path", "token"),
    [
        ("/asap/capability/execute", _token("agent+jwt", _NESTED)),
        ("/asap/capability/execute", _token("agent+jwt", b"[1]")),
        ("/asap/agent/register", _token("host+jwt", _NESTED)),
        ("/asap/agent/register", _token("host+jwt", _UNKNOWN_CURVE.encode())),
        ("/asap/agent/register", _self_signed_host_jwt(exp=10**400)),
    ],
    ids=["agent-nested", "agent-array", "host-nested", "host-unknown-curve", "host-exp-overflow"],
)
def test_route_answers_401_without_credentials(
    identity_client: TestClient, path: str, token: str
) -> None:
    r = identity_client.post(
        path, headers={"Authorization": f"Bearer {token}"}, json={"capability": "file:read"}
    )

    assert r.status_code == 401


def test_capability_list_ignores_nested_agent_jwt(identity_client: TestClient) -> None:
    """An invalid token on the list route falls back to the anonymous listing."""
    token = _token("agent+jwt", _NESTED)

    r = identity_client.get("/asap/capability/list", headers={"Authorization": f"Bearer {token}"})

    assert r.status_code == 200


async def test_mcp_bridge_answers_invalid_token() -> None:
    server = MCPServer()
    server.register_tool("echo", lambda message: message, {"type": "object"})
    config = MCPAuthConfig(
        host_store=InMemoryHostStore(),
        agent_store=InMemoryAgentStore(),
        capability_registry=CapabilityRegistry(),
    )
    protected = protect_server(server, config)

    result = await protected._handle_tools_call(
        tool_call_params("echo", arguments={}, jwt=_token("agent+jwt", _NESTED))
    )

    assert INVALID_TOKEN in error_text(result)
