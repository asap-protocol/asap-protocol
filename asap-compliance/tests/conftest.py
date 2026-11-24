"""Shared fixtures for ASAP compliance harness tests."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, cast

import httpx
import pytest

from asap.models.entities import Capability, Endpoint, Manifest, Skill
from asap.transport.server import create_app

from asap_compliance.config import ComplianceConfig

if TYPE_CHECKING:
    from fastapi import FastAPI

pytest_plugins = ["asap_compliance.pytest_plugin"]

TEST_RATE_LIMIT = "999999/minute"

# Live tests need examples/mcp_auth_http started with --compliance. They are skipped
# unless this variable holds its MCP URL; the live-flows CI job sets it.
LIVE_MCP_HTTP_URL_ENV = "ASAP_COMPLIANCE_MCP_HTTP_URL"
# Live A2A tests need `python -m examples.a2a_gateway.serve --compliance`. They are
# skipped unless this variable holds the gateway origin; the live-flows CI job sets it.
LIVE_A2A_GATEWAY_URL_ENV = "ASAP_COMPLIANCE_A2A_GATEWAY_URL"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        f"mcp_http_live: needs a live HTTP MCP server; runs when {LIVE_MCP_HTTP_URL_ENV} is set",
    )
    config.addinivalue_line(
        "markers",
        f"a2a_gateway_live: needs a live A2A gateway; runs when {LIVE_A2A_GATEWAY_URL_ENV} is set",
    )


@pytest.fixture
def live_mcp_http_url() -> str:
    """MCP URL of a live ``examples/mcp_auth_http`` server, or skip the test."""
    url = os.environ.get(LIVE_MCP_HTTP_URL_ENV, "")
    if not url:
        pytest.skip(f"set {LIVE_MCP_HTTP_URL_ENV} (example: http://127.0.0.1:8787/mcp)")
    return url


@pytest.fixture
def live_a2a_gateway_url() -> str:
    """Origin of a live ``examples/a2a_gateway`` served with ``--compliance``, or skip the test."""
    url = os.environ.get(LIVE_A2A_GATEWAY_URL_ENV, "")
    if not url:
        pytest.skip(f"set {LIVE_A2A_GATEWAY_URL_ENV} (example: http://127.0.0.1:8788)")
    return url


@pytest.fixture
def sample_manifest() -> Manifest:
    return Manifest(
        id="urn:asap:agent:compliance-test",
        name="Compliance Test Agent",
        version="1.0.0",
        description="Agent for handshake compliance tests",
        capabilities=Capability(
            asap_version="0.1",
            skills=[Skill(id="echo", description="Echo skill")],
            state_persistence=False,
        ),
        endpoints=Endpoint(asap="http://localhost:8000/asap"),
    )


@pytest.fixture
def good_agent_app(sample_manifest: Manifest) -> "FastAPI":
    return cast("FastAPI", create_app(sample_manifest, rate_limit=TEST_RATE_LIMIT))


@pytest.fixture
def compliance_config_with_transport(
    good_agent_app: "FastAPI",
) -> tuple[ComplianceConfig, httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=good_agent_app)
    client = httpx.AsyncClient(transport=transport, base_url="http://testserver")
    config = ComplianceConfig(
        agent_url="http://testserver",
        timeout_seconds=5.0,
        test_categories=["handshake"],
    )
    return config, client
