"""Tests for ``POST /registry/agents`` auto-registration handler."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from starlette.requests import Request
from starlette.testclient import TestClient

from asap.auth.middleware import OAuth2Claims
from asap.crypto.keys import generate_keypair
from asap.crypto.signing import sign_manifest
from asap.discovery.registry import RegistryEntry
from asap.discovery.validation import ManifestValidationError
from asap.errors import WebhookURLValidationError
from asap.models.entities import (
    Capability,
    Endpoint,
    HardwareCapability,
    InferenceCapability,
    Manifest,
    Skill,
)
from asap.models.enums import HardwareClass, HardwareIoType, InferenceMode
from asap.registry.auto_registration import (
    AutoRegistrationConfig,
    create_auto_registration_router,
    deterministic_registration_agent_id,
    fetch_manifest_at_url,
    harness_base_url_from_manifest,
    manifest_url_cache_key,
)
from asap.registry.bot_pr import BotPRResult, BotPRSettings
from asap.testing.compliance import CheckResult, ComplianceReport
from asap.transport.rate_limit import create_registration_rate_limiter, create_test_limiter
from asap.transport.rate_limit import registration_token_key
from asap.transport.server import create_app


async def _oauth_bypass(request: Request) -> OAuth2Claims:
    return OAuth2Claims(sub="urn:asap:agent:test-caller", scope=["asap:registry"], exp=9999999999)


@pytest.fixture
def manifest_https() -> Manifest:
    """Manifest whose ASAP endpoint maps to a public HTTPS harness base URL."""
    return Manifest(
        id="urn:asap:agent:ci:auto-reg-test",
        name="Auto Reg Test",
        version="1.0.0",
        description="CI auto-registration fixture",
        capabilities=Capability(
            asap_version="2.2.0",
            skills=[Skill(id="echo", description="Echo")],
            state_persistence=False,
        ),
        endpoints=Endpoint(asap="https://example.com/asap"),
    )


def _passing_report() -> ComplianceReport:
    return ComplianceReport(
        timestamp=datetime.now(timezone.utc),
        categories_run=["sanity"],
        checks=[
            CheckResult(name="dummy", category="test", passed=True, message="ok"),
        ],
        score=1.0,
        summary="1/1 checks passed (100%)",
    )


def _failing_report() -> ComplianceReport:
    return ComplianceReport(
        timestamp=datetime.now(timezone.utc),
        categories_run=["sanity"],
        checks=[
            CheckResult(name="dummy", category="test", passed=False, message="fail"),
        ],
        score=0.0,
        summary="0/1 checks passed (0%)",
    )


@pytest.fixture
def registration_app(
    manifest_https: Manifest,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[FastAPI, list[tuple[RegistryEntry, str]]]:
    """Minimal FastAPI app with registry routes and stubbed network calls."""
    pr_calls: list[tuple[RegistryEntry, str]] = []

    async def _fake_pr(entry: RegistryEntry, url: str) -> BotPRResult:
        pr_calls.append((entry, url))
        return BotPRResult(pr_url="https://github.com/o/r/pull/1", branch_name="auto-reg/x")

    async def _fake_fetch(_client: object, _url: str) -> Manifest:
        return manifest_https

    monkeypatch.setattr(
        "asap.registry.auto_registration.fetch_manifest_at_url",
        _fake_fetch,
    )

    cfg = AutoRegistrationConfig(
        oauth_claims_dependency=_oauth_bypass,
        run_compliance=lambda _base: _passing_report(),
        open_pull_request=_fake_pr,
    )
    app = FastAPI()
    app.state.registration_limiter = create_test_limiter(
        ["100000/hour"],
        key_func=registration_token_key,
    )
    app.state.registration_receipt_cache = {}
    app.include_router(create_auto_registration_router(cfg))
    return app, pr_calls


def test_register_agent_success_returns_receipt(
    registration_app: tuple[FastAPI, list[tuple[RegistryEntry, str]]],
    manifest_https: Manifest,
) -> None:
    app, pr_calls = registration_app
    client = TestClient(app)
    resp = client.post(
        "/registry/agents",
        json={"manifest_url": "https://example.com/.well-known/asap/manifest.json"},
        headers={"Authorization": "Bearer test-token-1"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["urn"] == manifest_https.id
    assert data["harness_score"] == 1.0
    assert data["trust_level"] == "self-signed"
    assert data["pr_url"] == "https://github.com/o/r/pull/1"
    assert data["status"] == "queued"
    assert data["agent_id"].startswith("urn:asap:registry:auto:")
    assert len(pr_calls) == 1
    entry, url = pr_calls[0]
    assert entry.id == manifest_https.id
    assert entry.verification is not None
    assert entry.verification.status.value == "pending"
    assert "self-signed" in entry.tags


def test_register_agent_derives_hardware_fields_in_pr_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Auto-registration PR entry mirrors manifest hardware/inference."""
    jetson_manifest = Manifest(
        id="urn:asap:agent:ci:jetson",
        name="Jetson Auto Reg",
        version="1.0.0",
        description="Edge fixture",
        capabilities=Capability(
            asap_version="2.1.0",
            skills=[Skill(id="gpio_control", description="GPIO")],
            hardware=HardwareCapability(
                class_=HardwareClass.EDGE_ACCELERATOR,
                io=[HardwareIoType.GPIO, HardwareIoType.I2C],
            ),
            inference=InferenceCapability(
                modes=[InferenceMode.CLOUD, InferenceMode.LOCAL_CUDA],
            ),
        ),
        endpoints=Endpoint(asap="https://example.com/asap"),
    )
    pr_calls: list[tuple[RegistryEntry, str]] = []

    async def _fake_fetch(_client: object, _url: str) -> Manifest:
        return jetson_manifest

    async def _fake_pr(entry: RegistryEntry, url: str) -> BotPRResult:
        pr_calls.append((entry, url))
        return BotPRResult(pr_url="https://github.com/o/r/pull/2", branch_name="auto-reg/y")

    monkeypatch.setattr(
        "asap.registry.auto_registration.fetch_manifest_at_url",
        _fake_fetch,
    )
    cfg = AutoRegistrationConfig(
        oauth_claims_dependency=_oauth_bypass,
        run_compliance=lambda _base: _passing_report(),
        open_pull_request=_fake_pr,
    )
    app = FastAPI()
    app.state.registration_limiter = create_test_limiter(
        ["100000/hour"],
        key_func=registration_token_key,
    )
    app.state.registration_receipt_cache = {}
    app.include_router(create_auto_registration_router(cfg))
    client = TestClient(app)
    resp = client.post(
        "/registry/agents",
        json={"manifest_url": "https://example.com/.well-known/asap/manifest.json"},
        headers={"Authorization": "Bearer test-token-jetson"},
    )
    assert resp.status_code == 200
    assert len(pr_calls) == 1
    entry, _ = pr_calls[0]
    assert entry.hardware_class == "edge_accelerator"
    assert entry.inference_modes == ["cloud", "local_cuda"]
    assert entry.hardware_io == ["gpio", "i2c"]


async def _noop_pr(_entry: RegistryEntry, _url: str) -> BotPRResult:
    raise AssertionError("PR must not run when compliance fails")


def test_register_agent_compliance_gate_422(
    manifest_https: Manifest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_fetch(_client: object, _url: str) -> Manifest:
        return manifest_https

    monkeypatch.setattr(
        "asap.registry.auto_registration.fetch_manifest_at_url",
        _fake_fetch,
    )

    cfg = AutoRegistrationConfig(
        oauth_claims_dependency=_oauth_bypass,
        run_compliance=lambda _base: _failing_report(),
        open_pull_request=_noop_pr,
    )

    app = FastAPI()
    app.state.registration_limiter = create_test_limiter(
        ["100000/hour"],
        key_func=registration_token_key,
    )
    app.state.registration_receipt_cache = {}
    app.include_router(create_auto_registration_router(cfg))

    client = TestClient(app)
    resp = client.post(
        "/registry/agents",
        json={"manifest_url": "https://example.com/.well-known/asap/manifest.json"},
        headers={"Authorization": "Bearer tok"},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["detail"]["error"] == "compliance_gate_failed"
    assert body["detail"]["score"] == 0.0


def test_register_agent_idempotent_cache(
    registration_app: tuple[FastAPI, list[tuple[RegistryEntry, str]]],
    manifest_https: Manifest,
) -> None:
    app, pr_calls = registration_app
    client = TestClient(app)
    url = "https://example.com/.well-known/asap/manifest.json"
    headers = {"Authorization": "Bearer same-token"}
    r1 = client.post("/registry/agents", json={"manifest_url": url}, headers=headers)
    r2 = client.post("/registry/agents", json={"manifest_url": url}, headers=headers)
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json() == r2.json()
    assert len(pr_calls) == 1


def test_create_app_wires_registry_auto_registration_router(
    manifest_https: Manifest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Factory configuration exposes the public registry registration route."""
    pr_calls: list[tuple[RegistryEntry, str]] = []

    async def _fake_fetch(_client: object, _url: str) -> Manifest:
        return manifest_https

    async def _fake_pr(entry: RegistryEntry, url: str) -> BotPRResult:
        pr_calls.append((entry, url))
        return BotPRResult(pr_url="https://github.com/o/r/pull/10", branch_name="auto-reg/10")

    monkeypatch.setattr(
        "asap.registry.auto_registration.fetch_manifest_at_url",
        _fake_fetch,
    )

    app = create_app(
        manifest_https,
        rate_limit="100000/minute",
        registry_auto_registration=AutoRegistrationConfig(
            oauth_claims_dependency=_oauth_bypass,
            run_compliance=lambda _base: _passing_report(),
            open_pull_request=_fake_pr,
        ),
        asap_challenge_enabled=False,
    )
    app.state.registration_limiter = create_test_limiter(
        ["100000/hour"],
        key_func=registration_token_key,
    )

    assert hasattr(app.state, "registration_receipt_cache")
    assert any(getattr(route, "path", None) == "/registry/agents" for route in app.routes)

    response = TestClient(app).post(
        "/registry/agents",
        json={"manifest_url": "https://example.com/.well-known/asap/manifest.json"},
        headers={"Authorization": "Bearer factory-token"},
    )

    assert response.status_code == 200
    assert response.json()["urn"] == manifest_https.id
    assert len(pr_calls) == 1


def test_deterministic_registration_agent_id_is_stable_and_normalized() -> None:
    """Registration ids remain stable across whitespace-normalized retries."""
    url = "https://example.com/.well-known/asap/manifest.json"

    assert deterministic_registration_agent_id(url) == deterministic_registration_agent_id(url)
    assert deterministic_registration_agent_id(f"  {url}  ") == deterministic_registration_agent_id(
        url
    )


def test_manifest_url_cache_key_matches_registration_agent_digest() -> None:
    """Receipt cache keys and deterministic ids share the same URL digest."""
    url = "https://example.com/.well-known/asap/manifest.json"
    digest = manifest_url_cache_key(url)

    assert deterministic_registration_agent_id(url) == f"urn:asap:registry:auto:{digest}"


def test_registration_rate_limit_sixth_request_429(
    manifest_https: Manifest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_fetch(_client: object, _url: str) -> Manifest:
        return manifest_https

    monkeypatch.setattr(
        "asap.registry.auto_registration.fetch_manifest_at_url",
        _fake_fetch,
    )

    async def _fake_pr(_entry: RegistryEntry, _url: str) -> BotPRResult:
        return BotPRResult(pr_url="https://github.com/o/r/pull/9", branch_name="b")

    cfg = AutoRegistrationConfig(
        oauth_claims_dependency=_oauth_bypass,
        run_compliance=lambda _base: _passing_report(),
        open_pull_request=_fake_pr,
    )
    app = FastAPI()
    app.state.registration_limiter = create_registration_rate_limiter()
    app.state.registration_receipt_cache = {}
    app.include_router(create_auto_registration_router(cfg))

    client = TestClient(app)
    headers = {"Authorization": "Bearer rl-token"}
    for i in range(5):
        body = {"manifest_url": f"https://example.org/rate-m{i}.json"}
        r = client.post("/registry/agents", json=body, headers=headers)
        assert r.status_code == 200, f"iteration {i}"
    r6 = client.post(
        "/registry/agents",
        json={"manifest_url": "https://example.org/rate-m5.json"},
        headers=headers,
    )
    assert r6.status_code == 429


def test_harness_base_url_strips_trailing_asap_segment(manifest_https: Manifest) -> None:
    m = manifest_https.model_copy(
        update={"endpoints": Endpoint(asap="https://agent.example/v1/asap")},
    )
    assert harness_base_url_from_manifest(m) == "https://agent.example/v1"


def test_harness_base_url_root_path_when_only_asap(manifest_https: Manifest) -> None:
    m = manifest_https.model_copy(
        update={"endpoints": Endpoint(asap="https://agent.example/asap")},
    )
    assert harness_base_url_from_manifest(m) == "https://agent.example"


def test_harness_base_url_preserves_path_without_asap_suffix(manifest_https: Manifest) -> None:
    m = manifest_https.model_copy(
        update={"endpoints": Endpoint(asap="https://agent.example/custom/path")},
    )
    assert harness_base_url_from_manifest(m) == "https://agent.example/custom/path"


@pytest.mark.asyncio
async def test_fetch_manifest_accepts_signed_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    """Signed manifest envelopes unwrap to inner Manifest (compliance harness parity)."""
    monkeypatch.setattr(
        "asap.registry.auto_registration.validate_callback_url",
        AsyncMock(return_value=None),
    )
    manifest = Manifest(
        id="urn:asap:agent:signed-auto-reg",
        name="Signed Auto Reg",
        version="1.0.0",
        description="Signed manifest for auto-registration fetch test",
        capabilities=Capability(
            asap_version="0.1",
            skills=[Skill(id="echo", description="Echo")],
            state_persistence=False,
        ),
        endpoints=Endpoint(asap="https://example.com/asap"),
    )
    private_key, _ = generate_keypair()
    signed_payload = sign_manifest(manifest, private_key).model_dump(mode="json")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=signed_payload)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await fetch_manifest_at_url(client, "https://example.com/m.json")
    assert result.id == "urn:asap:agent:signed-auto-reg"
    assert result.name == "Signed Auto Reg"


@pytest.mark.asyncio
async def test_fetch_manifest_rejects_tampered_signed_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tampered signed manifest envelope fails signature verification."""
    monkeypatch.setattr(
        "asap.registry.auto_registration.validate_callback_url",
        AsyncMock(return_value=None),
    )
    manifest = Manifest(
        id="urn:asap:agent:signed-auto-reg",
        name="Signed Auto Reg",
        version="1.0.0",
        description="Signed manifest for auto-registration fetch test",
        capabilities=Capability(
            asap_version="0.1",
            skills=[Skill(id="echo", description="Echo")],
            state_persistence=False,
        ),
        endpoints=Endpoint(asap="https://example.com/asap"),
    )
    private_key, _ = generate_keypair()
    signed_payload = sign_manifest(manifest, private_key).model_dump(mode="json")
    inner = signed_payload["manifest"]
    assert isinstance(inner, dict)
    inner["name"] = "Tampered Name"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=signed_payload)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ManifestValidationError, match="signature|verification|Invalid"):
            await fetch_manifest_at_url(client, "https://example.com/m.json")


@pytest.mark.asyncio
async def test_fetch_manifest_rejects_json_null(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "asap.registry.auto_registration.validate_callback_url",
        AsyncMock(return_value=None),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"null", headers={"content-type": "application/json"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(HTTPException) as excinfo:
            await fetch_manifest_at_url(client, "https://example.com/m.json")
    assert excinfo.value.status_code == 400
    assert "object" in str(excinfo.value.detail).lower()


@pytest.mark.asyncio
async def test_fetch_manifest_rejects_non_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "asap.registry.auto_registration.validate_callback_url",
        AsyncMock(return_value=None),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not-json")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(HTTPException) as excinfo:
            await fetch_manifest_at_url(client, "https://example.com/m.json")
    assert excinfo.value.status_code == 400
    assert "not JSON" in str(excinfo.value.detail)


@pytest.mark.asyncio
async def test_fetch_manifest_rejects_non_object_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "asap.registry.auto_registration.validate_callback_url",
        AsyncMock(return_value=None),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["not", "an", "object"])

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(HTTPException) as excinfo:
            await fetch_manifest_at_url(client, "https://example.com/m.json")
    assert excinfo.value.status_code == 400


@pytest.mark.asyncio
async def test_fetch_manifest_http_status_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "asap.registry.auto_registration.validate_callback_url",
        AsyncMock(return_value=None),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, request=request)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await fetch_manifest_at_url(client, "https://example.com/m.json")


@pytest.mark.asyncio
async def test_fetch_manifest_request_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "asap.registry.auto_registration.validate_callback_url",
        AsyncMock(return_value=None),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unreachable", request=request)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(httpx.ConnectError):
            await fetch_manifest_at_url(client, "https://example.com/m.json")


@pytest.mark.asyncio
async def test_fetch_manifest_rejects_redirect_without_following(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Redirect responses must not be followed (SSRF guard for manifest fetch)."""
    monkeypatch.setattr(
        "asap.registry.auto_registration.validate_callback_url",
        AsyncMock(return_value=None),
    )
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(
            302,
            headers={"Location": "http://169.254.169.254/latest/meta-data/"},
            request=request,
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await fetch_manifest_at_url(client, "https://example.com/m.json")
    assert requested == ["https://example.com/m.json"]
    assert not any("169.254.169.254" in url for url in requested)


@pytest.mark.asyncio
async def test_fetch_manifest_webhook_validation_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    async def deny(url: str, require_https: bool = True) -> None:
        raise WebhookURLValidationError(url, "blocked")

    monkeypatch.setattr(
        "asap.registry.auto_registration.validate_callback_url",
        deny,
    )
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={}))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(WebhookURLValidationError):
            await fetch_manifest_at_url(client, "https://example.com/m.json")


def test_register_agent_manifest_fetch_webhook_validation_400(
    manifest_https: Manifest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(_client: object, _url: str) -> Manifest:
        raise WebhookURLValidationError(_url, "ssrf")

    monkeypatch.setattr(
        "asap.registry.auto_registration.fetch_manifest_at_url",
        boom,
    )
    cfg = AutoRegistrationConfig(
        oauth_claims_dependency=_oauth_bypass,
        run_compliance=lambda _base: _passing_report(),
        open_pull_request=lambda _e, _u: BotPRResult(pr_url="x", branch_name="b"),
    )
    app = FastAPI()
    app.state.registration_limiter = create_test_limiter(
        ["100000/hour"],
        key_func=registration_token_key,
    )
    app.include_router(create_auto_registration_router(cfg))
    client = TestClient(app)
    resp = client.post(
        "/registry/agents",
        json={"manifest_url": "https://blocked.example/m.json"},
        headers={"Authorization": "Bearer t1"},
    )
    assert resp.status_code == 400


def test_register_agent_harness_base_url_blocked_400(
    manifest_https: Manifest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def validate_side_effect(url: str, require_https: bool = True) -> None:
        calls.append(url)
        if "evil-harness" in url:
            raise WebhookURLValidationError(url, "no")

    monkeypatch.setattr(
        "asap.registry.auto_registration.validate_callback_url",
        validate_side_effect,
    )

    evil = manifest_https.model_copy(
        update={"endpoints": Endpoint(asap="https://example.com/evil-harness/asap")},
    )

    async def _fake_fetch(_client: object, _url: str) -> Manifest:
        return evil

    monkeypatch.setattr(
        "asap.registry.auto_registration.fetch_manifest_at_url",
        _fake_fetch,
    )

    cfg = AutoRegistrationConfig(
        oauth_claims_dependency=_oauth_bypass,
        run_compliance=lambda _base: _passing_report(),
        open_pull_request=lambda _e, _u: BotPRResult(pr_url="x", branch_name="b"),
    )
    app = FastAPI()
    app.state.registration_limiter = create_test_limiter(
        ["100000/hour"],
        key_func=registration_token_key,
    )
    app.include_router(create_auto_registration_router(cfg))
    client = TestClient(app)
    resp = client.post(
        "/registry/agents",
        json={"manifest_url": "https://example.com/m.json"},
        headers={"Authorization": "Bearer t2"},
    )
    assert resp.status_code == 400
    assert any("evil-harness" in c for c in calls)


def test_register_agent_async_run_compliance_supported(
    manifest_https: Manifest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pr_calls: list[tuple[RegistryEntry, str]] = []

    async def _fake_fetch(_client: object, _url: str) -> Manifest:
        return manifest_https

    monkeypatch.setattr(
        "asap.registry.auto_registration.fetch_manifest_at_url",
        _fake_fetch,
    )

    async def async_report(_base: str) -> ComplianceReport:
        return _passing_report()

    async def _fake_pr(entry: RegistryEntry, url: str) -> BotPRResult:
        pr_calls.append((entry, url))
        return BotPRResult(pr_url="https://github.com/o/r/pull/2", branch_name="b")

    cfg = AutoRegistrationConfig(
        oauth_claims_dependency=_oauth_bypass,
        run_compliance=async_report,
        open_pull_request=_fake_pr,
    )
    app = FastAPI()
    app.state.registration_limiter = create_test_limiter(
        ["100000/hour"],
        key_func=registration_token_key,
    )
    app.include_router(create_auto_registration_router(cfg))
    client = TestClient(app)
    resp = client.post(
        "/registry/agents",
        json={"manifest_url": "https://example.com/m.json"},
        headers={"Authorization": "Bearer async-comp"},
    )
    assert resp.status_code == 200
    assert resp.json()["harness_score"] == 1.0


def test_register_agent_sync_open_pull_request_supported(
    manifest_https: Manifest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pr_calls: list[tuple[RegistryEntry, str]] = []

    async def _fake_fetch(_client: object, _url: str) -> Manifest:
        return manifest_https

    monkeypatch.setattr(
        "asap.registry.auto_registration.fetch_manifest_at_url",
        _fake_fetch,
    )

    def sync_pr(entry: RegistryEntry, url: str) -> BotPRResult:
        pr_calls.append((entry, url))
        return BotPRResult(pr_url="https://github.com/sync/pr", branch_name="br")

    cfg = AutoRegistrationConfig(
        oauth_claims_dependency=_oauth_bypass,
        run_compliance=lambda _b: _passing_report(),
        open_pull_request=sync_pr,
    )
    app = FastAPI()
    app.state.registration_limiter = create_test_limiter(
        ["100000/hour"],
        key_func=registration_token_key,
    )
    app.include_router(create_auto_registration_router(cfg))
    client = TestClient(app)
    resp = client.post(
        "/registry/agents",
        json={"manifest_url": "https://example.com/m.json"},
        headers={"Authorization": "Bearer sync-pr"},
    )
    assert resp.status_code == 200
    assert resp.json()["pr_url"] == "https://github.com/sync/pr"
    assert len(pr_calls) == 1


def test_register_agent_pr_backend_unconfigured_503(
    manifest_https: Manifest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_fetch(_client: object, _url: str) -> Manifest:
        return manifest_https

    monkeypatch.setattr(
        "asap.registry.auto_registration.fetch_manifest_at_url",
        _fake_fetch,
    )

    cfg = AutoRegistrationConfig(
        oauth_claims_dependency=_oauth_bypass,
        run_compliance=lambda _b: _passing_report(),
        open_pull_request=None,
        bot_settings=None,
    )
    app = FastAPI()
    app.state.registration_limiter = create_test_limiter(
        ["100000/hour"],
        key_func=registration_token_key,
    )
    app.include_router(create_auto_registration_router(cfg))
    client = TestClient(app)
    resp = client.post(
        "/registry/agents",
        json={"manifest_url": "https://example.com/m.json"},
        headers={"Authorization": "Bearer no-bot"},
    )
    assert resp.status_code == 503


def test_register_agent_pr_value_error_400(
    manifest_https: Manifest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_fetch(_client: object, _url: str) -> Manifest:
        return manifest_https

    monkeypatch.setattr(
        "asap.registry.auto_registration.fetch_manifest_at_url",
        _fake_fetch,
    )

    def bad_pr(_e: RegistryEntry, _u: str) -> BotPRResult:
        raise ValueError("invalid registry entry")

    cfg = AutoRegistrationConfig(
        oauth_claims_dependency=_oauth_bypass,
        run_compliance=lambda _b: _passing_report(),
        open_pull_request=bad_pr,
    )
    app = FastAPI()
    app.state.registration_limiter = create_test_limiter(
        ["100000/hour"],
        key_func=registration_token_key,
    )
    app.include_router(create_auto_registration_router(cfg))
    client = TestClient(app)
    resp = client.post(
        "/registry/agents",
        json={"manifest_url": "https://example.com/m.json"},
        headers={"Authorization": "Bearer bad-pr"},
    )
    assert resp.status_code == 400
    assert "invalid registry entry" in resp.json()["detail"]


def test_register_agent_pr_unexpected_error_502(
    manifest_https: Manifest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_fetch(_client: object, _url: str) -> Manifest:
        return manifest_https

    monkeypatch.setattr(
        "asap.registry.auto_registration.fetch_manifest_at_url",
        _fake_fetch,
    )

    def boom_pr(_e: RegistryEntry, _u: str) -> BotPRResult:
        raise RuntimeError("github unavailable")

    cfg = AutoRegistrationConfig(
        oauth_claims_dependency=_oauth_bypass,
        run_compliance=lambda _b: _passing_report(),
        open_pull_request=boom_pr,
    )
    app = FastAPI()
    app.state.registration_limiter = create_test_limiter(
        ["100000/hour"],
        key_func=registration_token_key,
    )
    app.include_router(create_auto_registration_router(cfg))
    client = TestClient(app)
    resp = client.post(
        "/registry/agents",
        json={"manifest_url": "https://example.com/m.json"},
        headers={"Authorization": "Bearer boom"},
    )
    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert detail == "Pull request flow failed. Check server logs for details."
    assert "github unavailable" not in detail


def test_register_agent_manifest_fetch_http_error_mapped_to_400(
    manifest_https: Manifest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_fetch(_client: object, _url: str) -> Manifest:
        req = httpx.Request("GET", _url)
        raise httpx.HTTPStatusError("404", request=req, response=httpx.Response(404, request=req))

    monkeypatch.setattr(
        "asap.registry.auto_registration.fetch_manifest_at_url",
        _fake_fetch,
    )

    cfg = AutoRegistrationConfig(
        oauth_claims_dependency=_oauth_bypass,
        run_compliance=lambda _b: _passing_report(),
        open_pull_request=lambda _e, _u: BotPRResult(pr_url="x", branch_name="b"),
    )
    app = FastAPI()
    app.state.registration_limiter = create_test_limiter(
        ["100000/hour"],
        key_func=registration_token_key,
    )
    app.include_router(create_auto_registration_router(cfg))
    client = TestClient(app)
    resp = client.post(
        "/registry/agents",
        json={"manifest_url": "https://example.com/missing.json"},
        headers={"Authorization": "Bearer fe"},
    )
    assert resp.status_code == 400
    assert "404" in resp.json()["detail"]


def test_register_agent_manifest_fetch_connect_error_502(
    manifest_https: Manifest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_fetch(_client: object, url: str) -> Manifest:
        req = httpx.Request("GET", url)
        raise httpx.ConnectError("timeout", request=req)

    monkeypatch.setattr(
        "asap.registry.auto_registration.fetch_manifest_at_url",
        _fake_fetch,
    )

    cfg = AutoRegistrationConfig(
        oauth_claims_dependency=_oauth_bypass,
        run_compliance=lambda _b: _passing_report(),
        open_pull_request=lambda _e, _u: BotPRResult(pr_url="x", branch_name="b"),
    )
    app = FastAPI()
    app.state.registration_limiter = create_test_limiter(
        ["100000/hour"],
        key_func=registration_token_key,
    )
    app.include_router(create_auto_registration_router(cfg))
    client = TestClient(app)
    resp = client.post(
        "/registry/agents",
        json={"manifest_url": "https://example.com/down.json"},
        headers={"Authorization": "Bearer fe2"},
    )
    assert resp.status_code == 502


def test_register_agent_manifest_validation_error_returns_400(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid manifest schema must map to HTTP 400, not an unhandled 500."""

    async def _fake_fetch(_client: object, _url: str) -> Manifest:
        raise ManifestValidationError(
            "capabilities is required",
            field="capabilities",
        )

    monkeypatch.setattr(
        "asap.registry.auto_registration.fetch_manifest_at_url",
        _fake_fetch,
    )

    cfg = AutoRegistrationConfig(
        oauth_claims_dependency=_oauth_bypass,
        run_compliance=lambda _b: _passing_report(),
        open_pull_request=lambda _e, _u: BotPRResult(pr_url="x", branch_name="b"),
    )
    app = FastAPI()
    app.state.registration_limiter = create_test_limiter(
        ["100000/hour"],
        key_func=registration_token_key,
    )
    app.include_router(create_auto_registration_router(cfg))
    client = TestClient(app)
    resp = client.post(
        "/registry/agents",
        json={"manifest_url": "https://example.com/invalid.json"},
        headers={"Authorization": "Bearer schema"},
    )
    assert resp.status_code == 400
    assert "capabilities" in resp.json()["detail"]


def test_register_agent_tampered_signed_manifest_returns_400(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tampered signed manifest from fetch maps to HTTP 400 through the route (#227)."""
    monkeypatch.setattr(
        "asap.registry.auto_registration.validate_callback_url",
        AsyncMock(return_value=None),
    )

    manifest = Manifest(
        id="urn:asap:agent:signed-route-reg",
        name="Signed Route Reg",
        version="1.0.0",
        description="Signed manifest for register route signature test",
        capabilities=Capability(
            asap_version="0.1",
            skills=[Skill(id="echo", description="Echo")],
            state_persistence=False,
        ),
        endpoints=Endpoint(asap="https://example.com/asap"),
    )
    private_key, _ = generate_keypair()
    signed_payload = sign_manifest(manifest, private_key).model_dump(mode="json")
    inner = signed_payload["manifest"]
    assert isinstance(inner, dict)
    inner["name"] = "Tampered Name"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=signed_payload)

    transport = httpx.MockTransport(handler)
    original_async_client = httpx.AsyncClient

    class _MockAsyncClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self._client = original_async_client(transport=transport)

        async def __aenter__(self) -> httpx.AsyncClient:
            return self._client

        async def __aexit__(self, *args: object, **kwargs: object) -> None:
            await self._client.aclose()

    monkeypatch.setattr("asap.registry.auto_registration.httpx.AsyncClient", _MockAsyncClient)

    cfg = AutoRegistrationConfig(
        oauth_claims_dependency=_oauth_bypass,
        run_compliance=lambda _b: _passing_report(),
        open_pull_request=lambda _e, _u: BotPRResult(pr_url="x", branch_name="b"),
    )
    app = FastAPI()
    app.state.registration_limiter = create_test_limiter(
        ["100000/hour"],
        key_func=registration_token_key,
    )
    app.include_router(create_auto_registration_router(cfg))
    client = TestClient(app)
    resp = client.post(
        "/registry/agents",
        json={"manifest_url": "https://example.com/tampered.json"},
        headers={"Authorization": "Bearer tampered"},
    )
    assert resp.status_code == 400
    assert "signature" in resp.json()["detail"].lower()


def test_register_agent_without_receipt_cache_still_ok(
    manifest_https: Manifest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pr_calls: list[tuple[RegistryEntry, str]] = []

    async def _fake_fetch(_client: object, _url: str) -> Manifest:
        return manifest_https

    monkeypatch.setattr(
        "asap.registry.auto_registration.fetch_manifest_at_url",
        _fake_fetch,
    )

    async def _fake_pr(entry: RegistryEntry, url: str) -> BotPRResult:
        pr_calls.append((entry, url))
        return BotPRResult(pr_url="https://github.com/o/r/pull/3", branch_name="b")

    cfg = AutoRegistrationConfig(
        oauth_claims_dependency=_oauth_bypass,
        run_compliance=lambda _b: _passing_report(),
        open_pull_request=_fake_pr,
    )
    app = FastAPI()
    app.state.registration_limiter = create_test_limiter(
        ["100000/hour"],
        key_func=registration_token_key,
    )
    app.include_router(create_auto_registration_router(cfg))
    client = TestClient(app)
    resp = client.post(
        "/registry/agents",
        json={"manifest_url": "https://example.com/nocache.json"},
        headers={"Authorization": "Bearer nc"},
    )
    assert resp.status_code == 200
    assert len(pr_calls) == 1


def test_register_agent_events_endpoint_in_registry_entry(
    manifest_https: Manifest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with_events = manifest_https.model_copy(
        update={
            "endpoints": Endpoint(
                asap="https://example.com/asap",
                events="wss://example.com/events",
            )
        }
    )
    pr_calls: list[tuple[RegistryEntry, str]] = []

    async def _fake_fetch(_client: object, _url: str) -> Manifest:
        return with_events

    monkeypatch.setattr(
        "asap.registry.auto_registration.fetch_manifest_at_url",
        _fake_fetch,
    )

    async def _fake_pr(entry: RegistryEntry, url: str) -> BotPRResult:
        pr_calls.append((entry, url))
        return BotPRResult(pr_url="x", branch_name="b")

    cfg = AutoRegistrationConfig(
        oauth_claims_dependency=_oauth_bypass,
        run_compliance=lambda _b: _passing_report(),
        open_pull_request=_fake_pr,
    )
    app = FastAPI()
    app.state.registration_limiter = create_test_limiter(
        ["100000/hour"],
        key_func=registration_token_key,
    )
    app.include_router(create_auto_registration_router(cfg))
    client = TestClient(app)
    resp = client.post(
        "/registry/agents",
        json={"manifest_url": "https://example.com/ws.json"},
        headers={"Authorization": "Bearer ws"},
    )
    assert resp.status_code == 200
    assert len(pr_calls) == 1
    entry, _ = pr_calls[0]
    assert entry.endpoints.get("ws") == "wss://example.com/events"


def test_register_agent_private_asap_endpoint_does_not_open_pr(
    manifest_https: Manifest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A loopback ``endpoints.asap`` must be rejected before a registry PR opens.

    The route uses the production SSRF check (not a stub) on the harness base
    derived from that URL.
    """
    pr_calls: list[tuple[RegistryEntry, str]] = []
    private = manifest_https.model_copy(
        update={"endpoints": Endpoint(asap="https://127.0.0.1/asap")}
    )

    async def _fake_fetch(_client: object, _url: str) -> Manifest:
        return private

    monkeypatch.setattr(
        "asap.registry.auto_registration.fetch_manifest_at_url",
        _fake_fetch,
    )

    async def _fake_pr(entry: RegistryEntry, url: str) -> BotPRResult:
        pr_calls.append((entry, url))
        return BotPRResult(pr_url="x", branch_name="b")

    cfg = AutoRegistrationConfig(
        oauth_claims_dependency=_oauth_bypass,
        run_compliance=lambda _b: _passing_report(),
        open_pull_request=_fake_pr,
    )
    app = FastAPI()
    app.state.registration_limiter = create_test_limiter(
        ["100000/hour"],
        key_func=registration_token_key,
    )
    app.include_router(create_auto_registration_router(cfg))
    client = TestClient(app)
    resp = client.post(
        "/registry/agents",
        json={"manifest_url": "https://example.com/loopback-asap.json"},
        headers={"Authorization": "Bearer loopback-asap"},
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "Harness base URL blocked" in detail
    assert "127.0.0.1" in detail
    assert pr_calls == []


def test_register_agent_blocked_events_endpoint_400(
    manifest_https: Manifest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Private WebSocket endpoints must not reach the registry PR payload."""
    pr_calls: list[tuple[RegistryEntry, str]] = []

    evil = manifest_https.model_copy(
        update={
            "endpoints": Endpoint(
                asap="https://example.com/asap",
                events="wss://127.0.0.1/events",
            )
        }
    )

    async def _fake_fetch(_client: object, _url: str) -> Manifest:
        return evil

    monkeypatch.setattr(
        "asap.registry.auto_registration.fetch_manifest_at_url",
        _fake_fetch,
    )

    async def _fake_pr(entry: RegistryEntry, url: str) -> BotPRResult:
        pr_calls.append((entry, url))
        return BotPRResult(pr_url="x", branch_name="b")

    cfg = AutoRegistrationConfig(
        oauth_claims_dependency=_oauth_bypass,
        run_compliance=lambda _b: _passing_report(),
        open_pull_request=_fake_pr,
    )
    app = FastAPI()
    app.state.registration_limiter = create_test_limiter(
        ["100000/hour"],
        key_func=registration_token_key,
    )
    app.include_router(create_auto_registration_router(cfg))
    client = TestClient(app)
    resp = client.post(
        "/registry/agents",
        json={"manifest_url": "https://example.com/bad-ws.json"},
        headers={"Authorization": "Bearer bad-ws"},
    )
    assert resp.status_code == 400
    assert "WebSocket endpoint blocked" in resp.json()["detail"]
    assert pr_calls == []


def test_run_compliance_bad_return_type_is_not_awaited(
    manifest_https: Manifest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_fetch(_client: object, _url: str) -> Manifest:
        return manifest_https

    monkeypatch.setattr(
        "asap.registry.auto_registration.fetch_manifest_at_url",
        _fake_fetch,
    )

    cfg = AutoRegistrationConfig(
        oauth_claims_dependency=_oauth_bypass,
        run_compliance=lambda _b: "not-a-report",
        open_pull_request=lambda _e, _u: BotPRResult(pr_url="x", branch_name="b"),
    )
    app = FastAPI()
    app.state.registration_limiter = create_test_limiter(
        ["100000/hour"],
        key_func=registration_token_key,
    )
    app.include_router(create_auto_registration_router(cfg))
    client = TestClient(app)
    with pytest.raises(TypeError, match="ComplianceReport"):
        client.post(
            "/registry/agents",
            json={"manifest_url": "https://example.com/bad-comp.json"},
            headers={"Authorization": "Bearer bc"},
        )


def test_open_pull_request_bad_return_type_returns_502(
    manifest_https: Manifest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_fetch(_client: object, _url: str) -> Manifest:
        return manifest_https

    monkeypatch.setattr(
        "asap.registry.auto_registration.fetch_manifest_at_url",
        _fake_fetch,
    )

    def weird_pr(_e: RegistryEntry, _u: str) -> str:
        return "nope"

    cfg = AutoRegistrationConfig(
        oauth_claims_dependency=_oauth_bypass,
        run_compliance=lambda _b: _passing_report(),
        open_pull_request=weird_pr,
    )
    app = FastAPI()
    app.state.registration_limiter = create_test_limiter(
        ["100000/hour"],
        key_func=registration_token_key,
    )
    app.include_router(create_auto_registration_router(cfg))
    client = TestClient(app)
    resp = client.post(
        "/registry/agents",
        json={"manifest_url": "https://example.com/weird-pr.json"},
        headers={"Authorization": "Bearer wp"},
    )
    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert detail == "Pull request flow failed. Check server logs for details."
    assert "nope" not in detail


def test_registration_skips_rate_limit_when_limiter_missing(
    manifest_https: Manifest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_fetch(_client: object, _url: str) -> Manifest:
        return manifest_https

    monkeypatch.setattr(
        "asap.registry.auto_registration.fetch_manifest_at_url",
        _fake_fetch,
    )

    cfg = AutoRegistrationConfig(
        oauth_claims_dependency=_oauth_bypass,
        run_compliance=lambda _b: _passing_report(),
        open_pull_request=lambda _e, _u: BotPRResult(pr_url="x", branch_name="b"),
    )
    app = FastAPI()
    app.include_router(create_auto_registration_router(cfg))
    client = TestClient(app)
    resp = client.post(
        "/registry/agents",
        json={"manifest_url": "https://example.com/no-lim.json"},
        headers={"Authorization": "Bearer nl"},
    )
    assert resp.status_code == 200


def test_default_compliance_uses_harness_from_url_when_not_injected(
    manifest_https: Manifest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_fetch(_client: object, _url: str) -> Manifest:
        return manifest_https

    monkeypatch.setattr(
        "asap.registry.auto_registration.fetch_manifest_at_url",
        _fake_fetch,
    )

    async def fake_from_url(
        base_url: str,
        *,
        request_timeout: float = 60.0,
        default_headers: dict[str, str] | None = None,
        categories: list[str] | None = None,
    ) -> ComplianceReport:
        assert base_url.startswith("https://")
        return _passing_report()

    monkeypatch.setattr(
        "asap.registry.auto_registration.run_compliance_harness_v2_from_url",
        fake_from_url,
    )

    cfg = AutoRegistrationConfig(
        oauth_claims_dependency=_oauth_bypass,
        run_compliance=None,
        open_pull_request=lambda _e, _u: BotPRResult(
            pr_url="https://pr/default-h", branch_name="br"
        ),
    )
    app = FastAPI()
    app.state.registration_limiter = create_test_limiter(
        ["100000/hour"],
        key_func=registration_token_key,
    )
    app.include_router(create_auto_registration_router(cfg))
    client = TestClient(app)
    resp = client.post(
        "/registry/agents",
        json={"manifest_url": "https://example.com/def-comp.json"},
        headers={"Authorization": "Bearer dc"},
    )
    assert resp.status_code == 200


def test_default_pr_opener_calls_open_registry_pull_request(
    manifest_https: Manifest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[RegistryEntry, str]] = []

    async def _fake_fetch(_client: object, _url: str) -> Manifest:
        return manifest_https

    monkeypatch.setattr(
        "asap.registry.auto_registration.fetch_manifest_at_url",
        _fake_fetch,
    )

    async def fake_open_pr(
        entry: RegistryEntry,
        *,
        manifest_url: str,
        settings: BotPRSettings,
        clone_url: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> BotPRResult:
        calls.append((entry, manifest_url))
        assert settings.owner == "acme"
        return BotPRResult(pr_url="https://github.com/acme/reg/pull/77", branch_name="auto-reg/x")

    monkeypatch.setattr(
        "asap.registry.auto_registration.open_registry_pull_request",
        fake_open_pr,
    )

    cfg = AutoRegistrationConfig(
        oauth_claims_dependency=_oauth_bypass,
        run_compliance=lambda _b: _passing_report(),
        open_pull_request=None,
        bot_settings=BotPRSettings(owner="acme", repo="reg", github_token="tok"),
    )
    app = FastAPI()
    app.state.registration_limiter = create_test_limiter(
        ["100000/hour"],
        key_func=registration_token_key,
    )
    app.include_router(create_auto_registration_router(cfg))
    client = TestClient(app)
    resp = client.post(
        "/registry/agents",
        json={"manifest_url": "https://example.com/def-pr.json"},
        headers={"Authorization": "Bearer dp"},
    )
    assert resp.status_code == 200
    assert resp.json()["pr_url"] == "https://github.com/acme/reg/pull/77"
    assert len(calls) == 1
