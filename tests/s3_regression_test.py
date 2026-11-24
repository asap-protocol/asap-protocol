"""Regression guards for security-critical behaviors.

Each test pins behavior that previously regressed or was identified during
review, so a future refactor cannot silently regress it:

* ``test_oauth2_http_and_ws_share_one_jwks_validator`` — the HTTP stack and WS
  ``app.state`` middleware must share ONE ``JWKSValidator`` so their JWKS caches
  cannot diverge.
* ``test_jwt_verify_result_capabilities_accessor_edge_cases`` — the additive
  ``JwtVerifyResult.capabilities`` accessor must return ``[]`` for absent or
  non-list claims and a filtered ``list[str]`` otherwise.
* ``test_identity_limiter_missing_returns_503_on_agent_register`` — parity on a
  second identity route (``/asap/agent/register``): a missing
  ``identity_limiter`` must 503 cleanly (was ``AttributeError`` -> 500).
* ``test_allow_env_jwt_fallback_emits_construction_warning`` — the process-wide
  JWT bypass flag warns at ``ProtectedMCPServer`` construction (via stderr
  ``warnings.warn``, not the stdout logger, so stdio is not corrupted).
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from asap.auth import OAuth2Config
from asap.auth.agent_jwt import JwtVerifyResult
from asap.auth.middleware import OAuth2Middleware
from asap.models.entities import Capability, Endpoint, Manifest, Skill


def _oauth2_only_manifest() -> Manifest:
    """Manifest with no ``auth`` block — OAuth2 is the sole auth path."""
    return Manifest(
        id="urn:asap:agent:s3-shared-jwks",
        name="S3 Shared JWKS Server",
        version="1.0.0",
        description="OAuth2-only server asserting one shared JWKSValidator",
        capabilities=Capability(
            asap_version="0.1",
            skills=[Skill(id="echo", description="Echo")],
            state_persistence=False,
        ),
        endpoints=Endpoint(asap="http://localhost:8000/asap"),
    )


def test_oauth2_http_and_ws_share_one_jwks_validator() -> None:
    """HTTP stack and WS ``app.state`` share ONE validator.

    ``_wire_middleware`` constructs a single ``shared_validator`` and passes it
    via ``validator=`` to BOTH the ``app.state.oauth2_middleware`` instance
    (read by the WS path) and the ``add_middleware(OAuth2Middleware, ...)``
    stack instance (HTTP). If a future refactor re-introduces two independent
    caches, the WS accept-time validation and the HTTP per-request validation
    can disagree on key-set freshness — the exact B4/C-2 security property S0
    closed. This test fails the moment the two instances stop sharing.
    """
    from asap.transport.server import create_app

    app = create_app(
        _oauth2_only_manifest(),
        oauth2_config=OAuth2Config(
            jwks_uri="https://issuer.example/.well-known/jwks.json",
            required_scope="asap:read",
        ),
    )
    # Force the middleware stack to materialize so user_middleware is populated.
    _ = TestClient(app)

    ws_middleware = getattr(app.state, "oauth2_middleware", None)
    assert isinstance(ws_middleware, OAuth2Middleware), (
        "app.state.oauth2_middleware must be an OAuth2Middleware instance "
        "(the WS path reads it for accept-time enforcement)"
    )

    stack_mw = [m for m in app.user_middleware if m.cls is OAuth2Middleware]
    assert stack_mw, "OAuth2Middleware must be registered on the HTTP stack"
    stack_validator = stack_mw[0].kwargs.get("validator")
    assert stack_validator is not None, (
        "the stack OAuth2Middleware must be wired with the shared validator"
    )

    # The security invariant: ONE JWKSValidator object reachable from both paths.
    assert ws_middleware._validator is stack_validator, (
        "WS app.state instance and HTTP stack instance must share the SAME "
        "JWKSValidator object — divergent caches are the S0 #237 landmine"
    )


@pytest.mark.parametrize(
    ("claims", "expected"),
    [
        (None, []),
        ({}, []),
        ({"sub": "host-1"}, []),
        ({"capabilities": "not-a-list"}, []),
        ({"capabilities": 42}, []),
        ({"capabilities": []}, []),
        ({"capabilities": ["web_search"]}, ["web_search"]),
        ({"capabilities": ["web_search", "code_exec"]}, ["web_search", "code_exec"]),
        # Non-string entries are filtered out (defensive typing).
        ({"capabilities": ["web_search", 7, None, "code_exec"]}, ["web_search", "code_exec"]),
    ],
)
def test_jwt_verify_result_capabilities_accessor_edge_cases(
    claims: dict[str, Any] | None, expected: list[str]
) -> None:
    """``JwtVerifyResult.capabilities`` returns a typed ``list[str]``.

    Absent / non-list / mixed-type claims collapse to a filtered ``list[str]``
    so the MCP Auth Bridge grant gate reads one typed shape instead of
    re-deriving the claim with ``isinstance`` at every call site.
    """
    result = JwtVerifyResult(ok=True, claims=claims)
    assert result.capabilities == expected


def test_identity_limiter_missing_returns_503_on_agent_register() -> None:
    """A second identity route also 503s cleanly when the limiter is missing.

    Mirrors ``TestIdentityLimiterMissingReturns503`` (capability/list) on the
    ``/asap/agent/register`` path so the ``Depends(require_identity_limiter)``
    migration is verified on more than one of the 9 migrated sites. Previously
    the inline ``request.app.state.identity_limiter.check(request)`` raised
    ``AttributeError`` (HTTP 500); the typed dependency converts it to 503.
    """
    from asap.auth.identity import InMemoryAgentStore, InMemoryHostStore
    from asap.transport.server import create_app

    manifest = _oauth2_only_manifest()
    agent_store = InMemoryAgentStore()
    host_store = InMemoryHostStore(agent_store=agent_store)
    app = create_app(
        manifest,
        rate_limit="999999/minute",
        identity_host_store=host_store,
        identity_agent_store=agent_store,
        identity_rate_limit="999999/minute",
    )
    # Simulate a misconfigured server: identity routes mounted but no limiter.
    if hasattr(app.state, "identity_limiter"):
        delattr(app.state, "identity_limiter")
    client = TestClient(app)

    r = client.post("/asap/agent/register")

    assert r.status_code == 503
    assert "identity_limiter not set" in r.json()["detail"]


def test_allow_env_jwt_fallback_emits_construction_warning() -> None:
    """``allow_env_jwt_fallback=True`` warns at ``ProtectedMCPServer`` construction.

    The warning goes to stderr via ``warnings.warn`` (not the stdout structlog
    logger) so it does not corrupt the JSON-RPC stream when the server runs as a
    stdio subprocess — the failure mode that forced the Wave C attempt to revert
    a stdout log at ``MCPAuthConfig`` construction. Operators get a loud signal
    before deploying a process-wide JWT bypass in multi-tool production.
    """
    import warnings

    from asap.auth.identity import InMemoryAgentStore, InMemoryHostStore
    from asap.auth.capabilities import CapabilityRegistry
    from asap.mcp.auth import MCPAuthConfig, ProtectedMCPServer

    config = MCPAuthConfig(
        host_store=InMemoryHostStore(agent_store=InMemoryAgentStore()),
        agent_store=InMemoryAgentStore(),
        capability_registry=CapabilityRegistry(),
        allow_env_jwt_fallback=True,
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        ProtectedMCPServer(config)

    fallback_warnings = [w for w in caught if "allow_env_jwt_fallback" in str(w.message)]
    assert len(fallback_warnings) == 1, (
        "expected exactly one allow_env_jwt_fallback construction warning, "
        f"got {len(fallback_warnings)}: {[str(w.message) for w in fallback_warnings]}"
    )
    assert "unsafe for multi-tool production" in str(fallback_warnings[0].message)
