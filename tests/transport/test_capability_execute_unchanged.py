"""``POST /asap/capability/execute`` answers exactly as it did before.

The expected responses were recorded from the route before the external-token
change (commit 050130cc). Request ids and agent ids are random, so they are
compared as placeholders; Pydantic errors are compared as ``type`` and ``loc``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from asap.auth.agent_jwt import create_host_jwt
from tests.auth.local_issuer import LocalIssuer
from tests.transport.test_capability_routes import (
    _DEFAULT_CAPS,
    _HOST_JWT_AUDIENCE,
    _agent_jwt,
    _register_and_activate,
    _setup,
)

if TYPE_CHECKING:
    from asap.models.entities import Manifest
    from asap.transport.rate_limit import ASAPRateLimiter

_ASAP_CHALLENGE = 'ASAP discovery="http://localhost:8000/.well-known/asap/manifest.json"'
_CHALLENGE = f"Bearer, {_ASAP_CHALLENGE}"
_NOT_GRANTED = {
    "error": {
        "code": "capability_not_granted",
        "message": "agent has no active grant for 'file:read'",
        "data": {"required_capability": "file:read"},
        "request_id": "<request_id>",
    }
}
_VIOLATED = {
    "error": "constraint_violated",
    "capability": "file:read",
    "violations": [
        {
            "field": "path",
            "operator": "in",
            "message": "path: '/etc' not in allowed values ['/tmp']",
        }
    ],
    "request_id": "<request_id>",
}

# name -> (status, normalized body, WWW-Authenticate or None)
EXPECTED_AGENT_PATH: dict[str, tuple[int, Any, str | None]] = {
    "granted": (200, {"capability": "file:read", "status": "executed", "agent_id": "<agent_id>"}, None),
    "not granted": (403, _NOT_GRANTED, None),
    "constraint violated": (403, _VIOLATED, None),
    "no bearer": (401, {"detail": "Authentication required"}, _CHALLENGE),
    "bad json": (400, {"detail": "Invalid JSON: Expecting value: line 1 column 1 (char 0)"}, None),
    "bad body": (400, {"detail": [{"type": "missing", "loc": ["capability"]}]}, None),
}  # fmt: skip
# Bearers that are not Agent JWTs; with the external-token config set they
# take the external path instead.
EXPECTED_NON_AGENT_BEARER: dict[str, tuple[int, Any, str | None]] = {
    "host jwt": (401, {"detail": "invalid typ for agent JWT", "request_id": "<request_id>"}, _ASAP_CHALLENGE),
    "external token": (401, {"detail": "invalid typ for agent JWT", "request_id": "<request_id>"}, _ASAP_CHALLENGE),
    "opaque token": (401, {"detail": "invalid JWT structure: decode_error: Invalid JSON Web Signature", "request_id": "<request_id>"}, _ASAP_CHALLENGE),
}  # fmt: skip


def _normalize(body: Any, agent_id: str) -> Any:
    if not isinstance(body, dict):
        return body
    out: dict[str, Any] = {}
    for key, value in body.items():
        if key == "request_id":
            out[key] = "<request_id>"
        elif key == "agent_id" and value == agent_id:
            out[key] = "<agent_id>"
        elif key == "detail" and isinstance(value, list):
            out[key] = [{"type": item["type"], "loc": item["loc"]} for item in value]
        else:
            out[key] = _normalize(value, agent_id)
    return out


async def execute_case(
    client: TestClient, app: Any, agent_store: Any, registry: Any, name: str
) -> tuple[int, Any, str | None]:
    """Send the request for case ``name`` and return (status, normalized body, challenge)."""
    host_sk, agent_sk = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    aid = await _register_and_activate(client, app, agent_store, host_sk, agent_sk)
    if name == "granted":
        registry.grant(aid, "file:read")
    if name == "constraint violated":
        registry.grant(aid, "file:read", constraints={"path": {"in": ["/tmp"]}})
    bearer = {
        "no bearer": None,
        "host jwt": create_host_jwt(host_sk, aud=_HOST_JWT_AUDIENCE),
        "external token": LocalIssuer().mint(LocalIssuer().claims(_HOST_JWT_AUDIENCE)),
        "opaque token": "opaque-token-value",
    }.get(name, _agent_jwt(agent_sk, host_sk, aid))
    headers = {"Authorization": f"Bearer {bearer}"} if bearer else {}
    arguments = {"path": "/etc"} if name == "constraint violated" else None
    if name == "bad json":
        response = client.post("/asap/capability/execute", headers=headers, content=b"x")
    else:
        body = (
            {"arguments": {}}
            if name == "bad body"
            else {"capability": "file:read", "arguments": arguments}
        )
        response = client.post("/asap/capability/execute", headers=headers, json=body)
    challenge = response.headers.get("www-authenticate")
    return response.status_code, _normalize(response.json(), aid), challenge


@pytest.mark.filterwarnings("ignore:EdDSA is deprecated:UserWarning")
@pytest.mark.parametrize("name", [*EXPECTED_AGENT_PATH, *EXPECTED_NON_AGENT_BEARER])
async def test_execute_answers_as_before_without_the_external_config(
    sample_manifest: Manifest, isolated_rate_limiter: ASAPRateLimiter | None, name: str
) -> None:
    app, agent_store, _, registry = _setup(
        sample_manifest, isolated_rate_limiter, capabilities=_DEFAULT_CAPS
    )
    expected = {**EXPECTED_AGENT_PATH, **EXPECTED_NON_AGENT_BEARER}[name]
    assert await execute_case(TestClient(app), app, agent_store, registry, name) == expected
