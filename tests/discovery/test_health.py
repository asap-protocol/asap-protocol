"""Tests for health endpoint handler and client health_check method."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import httpx
import pytest
from fastapi.testclient import TestClient

from asap.discovery import health
from asap.models.entities import Manifest
from asap.transport.client import ASAPClient
from asap.transport.server import create_app

if TYPE_CHECKING:
    from fastapi import FastAPI


@pytest.fixture
def app(sample_manifest: Manifest) -> "FastAPI":
    """Create FastAPI app with sample manifest."""
    return create_app(sample_manifest, rate_limit="999999/minute")


@pytest.fixture
def client(app: "FastAPI") -> TestClient:
    """Create test client for the app."""
    return TestClient(app)


class TestHealthResponse:
    """Tests for get_health_response and build_health_json_response."""

    def test_healthy_response_has_required_fields(self, sample_manifest: Manifest) -> None:
        """Health response includes status, agent_id, version, asap_version, uptime_seconds."""
        # started_at must be monotonic (health uses time.monotonic() for uptime)
        started_at = time.monotonic() - 10.0
        health_status, status_code = health.get_health_response(sample_manifest, started_at)
        assert status_code == 200
        assert health_status.status == "healthy"
        assert health_status.agent_id == sample_manifest.id
        assert health_status.version == sample_manifest.version
        assert health_status.asap_version == sample_manifest.capabilities.asap_version
        assert health_status.uptime_seconds >= 9.0
        assert health_status.uptime_seconds <= 11.0

    def test_unhealthy_returns_503(self, sample_manifest: Manifest) -> None:
        """When is_healthy=False, status_code is 503."""
        started_at = time.monotonic()
        health_status, status_code = health.get_health_response(
            sample_manifest, started_at, is_healthy=False
        )
        assert status_code == 503
        assert health_status.status == "unhealthy"

    def test_health_response_includes_optional_load(self, sample_manifest: Manifest) -> None:
        """Health response can include load metrics."""
        load = health.HealthLoad(active_tasks=3, queue_depth=2)
        health_status, status_code = health.get_health_response(
            sample_manifest, time.monotonic(), load=load
        )
        assert status_code == 200
        assert health_status.load is not None
        assert health_status.load.active_tasks == 3
        assert health_status.load.queue_depth == 2

    def test_future_monotonic_start_reports_zero_uptime(self, sample_manifest: Manifest) -> None:
        """A start timestamp ahead of the clock must not fail the health probe."""
        started_at = time.monotonic() + 60.0
        assert health.compute_uptime_seconds(started_at) == 0.0
        health_status, status_code = health.get_health_response(sample_manifest, started_at)
        assert status_code == 200
        assert health_status.status == "healthy"
        assert health_status.uptime_seconds == 0.0


class TestHealthEndpointIntegration:
    """Tests for GET /.well-known/asap/health served by ASAPServer."""

    def test_health_endpoint_returns_valid_json(
        self, client: TestClient, sample_manifest: Manifest
    ) -> None:
        """Endpoint returns valid JSON with correct fields."""
        response = client.get("/.well-known/asap/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["agent_id"] == sample_manifest.id
        assert data["version"] == sample_manifest.version
        assert data["asap_version"] == sample_manifest.capabilities.asap_version
        assert "uptime_seconds" in data
        assert data["uptime_seconds"] >= 0

    def test_health_endpoint_content_type(self, client: TestClient) -> None:
        """Response has Content-Type application/json."""
        response = client.get("/.well-known/asap/health")
        assert response.status_code == 200
        assert "application/json" in response.headers.get("content-type", "")

    def test_uptime_increases_over_time(
        self, client: TestClient, sample_manifest: Manifest
    ) -> None:
        """Uptime increases between requests."""
        r1 = client.get("/.well-known/asap/health")
        assert r1.status_code == 200
        uptime1 = r1.json()["uptime_seconds"]
        time.sleep(0.1)
        r2 = client.get("/.well-known/asap/health")
        assert r2.status_code == 200
        uptime2 = r2.json()["uptime_seconds"]
        assert uptime2 >= uptime1


class TestClientHealthCheck:
    """Tests for ASAPClient.health_check()."""

    @pytest.mark.asyncio
    async def test_health_check_parses_response(self, sample_manifest: Manifest) -> None:
        """Client health_check parses response into HealthStatus."""
        app = create_app(sample_manifest, rate_limit="999999/minute")
        transport = httpx.ASGITransport(app=app)
        async with ASAPClient(
            "http://testserver",
            transport=transport,
            require_https=False,
        ) as client:
            status = await client.health_check("http://testserver")

        assert status.status == "healthy"
        assert status.agent_id == sample_manifest.id
        assert status.version == sample_manifest.version
        assert status.uptime_seconds >= 0

    @pytest.mark.asyncio
    async def test_health_check_defaults_to_client_base_url(
        self, sample_manifest: Manifest
    ) -> None:
        """health_check without base_url uses the client's HTTP base URL."""
        app = create_app(sample_manifest, rate_limit="999999/minute")
        transport = httpx.ASGITransport(app=app)
        async with ASAPClient(
            "http://testserver",
            transport=transport,
            require_https=False,
        ) as client:
            status = await client.health_check()

        assert status.status == "healthy"
        assert status.agent_id == sample_manifest.id

    @pytest.mark.asyncio
    async def test_health_check_http_error_raises(self, sample_manifest: Manifest) -> None:
        """health_check raises ASAPConnectionError on HTTP 404."""
        from asap.transport.client import ASAPConnectionError

        app = create_app(sample_manifest, rate_limit="999999/minute")
        transport = httpx.ASGITransport(app=app)
        async with ASAPClient(
            "http://testserver",
            transport=transport,
            require_https=False,
        ) as client:
            with pytest.raises(ASAPConnectionError, match="404"):
                await client.health_check("http://testserver/nonexistent")


class TestManifestTtlSeconds:
    """Tests for manifest ttl_seconds field."""

    def test_manifest_has_ttl_seconds_default(self, sample_manifest: Manifest) -> None:
        """Manifest serializes with default ttl_seconds."""
        assert sample_manifest.ttl_seconds == 300
        assert "ttl_seconds" in sample_manifest.model_dump()

    def test_manifest_ttl_seconds_custom(self) -> None:
        """Manifest accepts custom ttl_seconds."""
        from asap.models.entities import Capability, Endpoint, Skill

        m = Manifest(
            id="urn:asap:agent:custom-ttl",
            name="Custom TTL",
            version="1.0.0",
            description="Test",
            capabilities=Capability(
                asap_version="0.1",
                skills=[Skill(id="x", description="x")],
                state_persistence=False,
            ),
            endpoints=Endpoint(asap="http://localhost:8000/asap"),
            ttl_seconds=60,
        )
        assert m.ttl_seconds == 60
