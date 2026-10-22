"""Route-level idempotency window for ``POST /registry/agents``."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from starlette.requests import Request
from starlette.testclient import TestClient

from asap.auth.middleware import OAuth2Claims
from asap.discovery.registry import RegistryEntry
from asap.models.entities import Capability, Endpoint, Manifest, Skill
from asap.registry.auto_registration import AutoRegistrationConfig, create_auto_registration_router
from asap.registry.bot_pr import BotPRResult
from asap.registry.receipt_cache import create_registration_receipt_cache
from asap.testing.compliance import CheckResult, ComplianceReport
from asap.transport.rate_limit import create_test_limiter, registration_token_key

_MANIFEST_URL = "https://example.com/.well-known/asap/manifest.json"
_TTL_SECONDS = 10.0


def _manifest() -> Manifest:
    """Public HTTPS manifest used only as a stubbed fetch result."""
    return Manifest(
        id="urn:asap:agent:ci:receipt-route",
        name="Receipt Route",
        version="1.0.0",
        description="Registration receipt TTL route fixture",
        capabilities=Capability(
            asap_version="2.2.0",
            skills=[Skill(id="echo", description="Echo")],
            state_persistence=False,
        ),
        endpoints=Endpoint(asap="https://example.com/asap"),
    )


def _passing_report() -> ComplianceReport:
    """Harness report that clears the registration score gate."""
    return ComplianceReport(
        timestamp=datetime(2026, 6, 1, tzinfo=timezone.utc),
        categories_run=["sanity"],
        checks=[CheckResult(name="dummy", category="test", passed=True, message="ok")],
        score=1.0,
        summary="1/1 checks passed (100%)",
    )


async def _oauth_bypass(_request: Request) -> OAuth2Claims:
    """Skip real bearer validation; the route still requires a dependency."""
    return OAuth2Claims(
        sub="urn:asap:agent:test-caller",
        scope=["asap:registry"],
        exp=9_999_999_999,
    )


async def _allow_endpoint(_url: str, *, require_https: bool = True) -> None:
    """Skip DNS SSRF checks; this test locks the receipt cache, not URL policy."""
    _ = require_https


def _clock(monkeypatch: pytest.MonkeyPatch) -> dict[str, float]:
    """Pin receipt-cache monotonic time so TTL does not depend on the runner."""
    clock = {"t": 0.0}
    monkeypatch.setattr("asap.registry.receipt_cache.time.monotonic", lambda: clock["t"])
    return clock


def _patch_registration_io(
    monkeypatch: pytest.MonkeyPatch,
    manifest: Manifest,
    fetches: list[str],
    pr_urls: list[str],
) -> Callable[[RegistryEntry, str], Awaitable[BotPRResult]]:
    """Stub manifest fetch and SSRF checks, and return the pull-request opener."""

    async def _fetch(_client: object, url: str) -> Manifest:
        fetches.append(url)
        return manifest

    async def _open_pr(_entry: RegistryEntry, _url: str) -> BotPRResult:
        pr_url = f"https://github.com/o/r/pull/{len(pr_urls) + 1}"
        pr_urls.append(pr_url)
        return BotPRResult(pr_url=pr_url, branch_name=f"auto-reg/{len(pr_urls)}")

    monkeypatch.setattr("asap.registry.auto_registration.fetch_manifest_at_url", _fetch)
    monkeypatch.setattr("asap.registry.auto_registration.validate_callback_url", _allow_endpoint)
    monkeypatch.setattr(
        "asap.registry.auto_registration.validate_agent_endpoint_url",
        _allow_endpoint,
    )
    return _open_pr


def _app(
    monkeypatch: pytest.MonkeyPatch,
    manifest: Manifest,
) -> tuple[TestClient, list[str], list[str]]:
    """Build a registration app whose receipt cache expires after 10 seconds."""
    fetches: list[str] = []
    pr_urls: list[str] = []
    open_pr = _patch_registration_io(monkeypatch, manifest, fetches, pr_urls)
    app = FastAPI()
    app.state.registration_limiter = create_test_limiter(
        ["100000/hour"],
        key_func=registration_token_key,
    )
    app.state.registration_receipt_cache = create_registration_receipt_cache(
        maxsize=8,
        ttl=_TTL_SECONDS,
    )
    app.include_router(
        create_auto_registration_router(
            AutoRegistrationConfig(
                oauth_claims_dependency=_oauth_bypass,
                run_compliance=lambda _base: _passing_report(),
                open_pull_request=open_pr,
            )
        )
    )
    return TestClient(app), fetches, pr_urls


def _post(client: TestClient) -> dict[str, object]:
    """POST the same manifest URL and return the JSON body."""
    response = client.post(
        "/registry/agents",
        json={"manifest_url": _MANIFEST_URL},
        headers={"Authorization": "Bearer receipt-token"},
    )
    assert response.status_code == 200
    body: dict[str, object] = response.json()
    return body


def test_registration_receipt_expires_on_the_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second POST inside the TTL reuses the receipt; at the TTL it opens a new PR.

    The handler consults ``registration_receipt_cache`` via ``in`` and
    ``__getitem__``. A plain dict would keep the first pull request forever.
    """
    clock = _clock(monkeypatch)
    client, fetches, pr_urls = _app(monkeypatch, _manifest())

    first = _post(client)
    assert first["pr_url"] == "https://github.com/o/r/pull/1"
    assert fetches == [_MANIFEST_URL]
    assert pr_urls == ["https://github.com/o/r/pull/1"]

    clock["t"] = _TTL_SECONDS - 1
    replay = _post(client)
    assert replay == first
    assert fetches == [_MANIFEST_URL]
    assert pr_urls == ["https://github.com/o/r/pull/1"]

    clock["t"] = _TTL_SECONDS
    renewed = _post(client)
    assert renewed["pr_url"] == "https://github.com/o/r/pull/2"
    assert renewed["agent_id"] == first["agent_id"]
    assert fetches == [_MANIFEST_URL, _MANIFEST_URL]
    assert pr_urls == [
        "https://github.com/o/r/pull/1",
        "https://github.com/o/r/pull/2",
    ]
