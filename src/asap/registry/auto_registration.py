"""HTTP handler for ``POST /registry/agents`` self-service Lite Registry registration."""

from __future__ import annotations

import hashlib
import inspect
import logging
from collections.abc import Awaitable, Callable, MutableMapping
from dataclasses import dataclass
from typing import Any, Literal, cast
from urllib.parse import urlparse, urlunparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from asap.auth.middleware import OAuth2Claims
from asap.auth.scopes import require_scope
from asap.discovery.registry import RegistryEntry, generate_registry_entry
from asap.discovery.validation import (
    ManifestValidationError,
    validate_signed_manifest_response,
)
from asap.errors import SignatureVerificationError, WebhookURLValidationError
from asap.models.entities import Manifest
from asap.registry.anti_spam import TRUST_LEVEL_SELF_SIGNED, auto_register_verification
from asap.registry.bot_pr import BotPRResult, BotPRSettings, open_registry_pull_request
from asap.testing.compliance import ComplianceReport, run_compliance_harness_v2_from_url
from asap.transport.rate_limit import RateLimitExceeded
from asap.transport.webhook import validate_callback_url

logger = logging.getLogger(__name__)

REGISTRY_REGISTER_SCOPE = "asap:registry"


@dataclass
class AutoRegistrationConfig:
    """Pluggable behaviour for :func:`create_auto_registration_router`."""

    required_scope: str = REGISTRY_REGISTER_SCOPE
    oauth_claims_dependency: Callable[..., OAuth2Claims | Awaitable[OAuth2Claims]] | None = None
    run_compliance: Callable[[str], ComplianceReport | Awaitable[ComplianceReport]] | None = None
    open_pull_request: (
        Callable[[RegistryEntry, str], BotPRResult | Awaitable[BotPRResult]] | None
    ) = None
    bot_settings: BotPRSettings | None = None
    httpx_timeout: float = 60.0


class RegisterAgentRequest(BaseModel):
    """Payload for ``POST /registry/agents``."""

    model_config = ConfigDict(extra="forbid")

    manifest_url: HttpUrl


class ComplianceFailedDetail(BaseModel):
    """422 payload when Compliance Harness v2 score < 1.0."""

    model_config = ConfigDict(extra="forbid")

    error: Literal["compliance_gate_failed"] = "compliance_gate_failed"
    score: float
    failed_checks: list[dict[str, Any]]
    summary: str


class RegistrationReceipt(BaseModel):
    """Successful registration response (AUTO-007)."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(
        ..., description="Deterministic idempotency key derived from manifest URL"
    )
    urn: str = Field(..., description="Agent URN from manifest")
    harness_score: float
    pr_url: str | None = None
    status: Literal["queued", "merged", "verified-pending"] = "queued"
    trust_level: str = Field(default=TRUST_LEVEL_SELF_SIGNED)


def deterministic_registration_agent_id(manifest_url: str) -> str:
    """Return stable agent id for idempotent registration receipts."""
    normalized = str(manifest_url).strip()
    digest = hashlib.sha256(normalized.encode()).hexdigest()
    return f"urn:asap:registry:auto:{digest}"


def manifest_url_cache_key(manifest_url: str) -> str:
    return hashlib.sha256(manifest_url.strip().encode()).hexdigest()


def harness_base_url_from_manifest(manifest: Manifest) -> str:
    """Derive agent root URL for Compliance Harness from ``endpoints.asap``."""
    u = urlparse(manifest.endpoints.asap)
    path = u.path.rstrip("/")
    if path.endswith("/asap"):
        path = path[: -len("/asap")]
    if not path:
        path = "/"
    base = urlunparse((u.scheme, u.netloc, path, "", "", "")).rstrip("/")
    return base or f"{u.scheme}://{u.netloc}"


async def _registration_rate_limit(request: Request) -> None:
    limiter = getattr(request.app.state, "registration_limiter", None)
    if limiter is None:
        return
    try:
        limiter.check(request)
    except RateLimitExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail=exc.detail,
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc


async def fetch_manifest_at_url(client: httpx.AsyncClient, manifest_url: str) -> Manifest:
    """Fetch manifest JSON after SSRF validation (HTTPS, DNS rebinding guard)."""
    await validate_callback_url(manifest_url, require_https=True)
    response = await client.get(manifest_url, follow_redirects=False)
    response.raise_for_status()
    try:
        data = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Manifest response is not JSON") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Manifest JSON must be an object")
    try:
        return validate_signed_manifest_response(data, verify_signature=True)
    except SignatureVerificationError as exc:
        raise ManifestValidationError(str(exc), field="signature") from exc


def _build_registry_entry(manifest: Manifest, manifest_url: str) -> RegistryEntry:
    endpoints: dict[str, str] = {
        "http": manifest.endpoints.asap,
        "manifest": manifest_url,
    }
    if manifest.endpoints.events:
        endpoints["ws"] = manifest.endpoints.events
    entry = generate_registry_entry(
        manifest,
        endpoints,
    )
    tags = sorted({*entry.tags, TRUST_LEVEL_SELF_SIGNED})
    return entry.model_copy(
        update={
            "verification": auto_register_verification(),
            "tags": tags,
        }
    )


def create_auto_registration_router(config: AutoRegistrationConfig | None = None) -> APIRouter:
    """Create router with ``POST /registry/agents``."""
    cfg = config or AutoRegistrationConfig()
    claims_dep = cfg.oauth_claims_dependency or require_scope(cfg.required_scope)

    async def _run_harness(base_url: str) -> ComplianceReport:
        if cfg.run_compliance is not None:
            result = cfg.run_compliance(base_url)
            if inspect.isawaitable(result):
                return await result
            if isinstance(result, ComplianceReport):
                return result
            raise TypeError(
                "run_compliance must return ComplianceReport or Awaitable[ComplianceReport]"
            )
        return await run_compliance_harness_v2_from_url(
            base_url,
            request_timeout=cfg.httpx_timeout,
        )

    async def _open_pr(entry: RegistryEntry, manifest_url: str) -> BotPRResult:
        if cfg.open_pull_request is not None:
            result = cfg.open_pull_request(entry, manifest_url)
            if inspect.isawaitable(result):
                return await result
            if isinstance(result, BotPRResult):
                return result
            raise TypeError("open_pull_request must return BotPRResult or Awaitable[BotPRResult]")
        if cfg.bot_settings is None:
            raise HTTPException(
                status_code=503,
                detail="Auto-registration PR backend is not configured (missing BotPRSettings)",
            )
        return await open_registry_pull_request(
            entry,
            manifest_url=manifest_url,
            settings=cfg.bot_settings,
        )

    router = APIRouter(prefix="/registry", tags=["registry"])

    @router.post(
        "/agents",
        response_model=RegistrationReceipt,
        responses={422: {"model": ComplianceFailedDetail}},
    )
    async def register_agent(
        request: Request,
        body: RegisterAgentRequest,
        _claims: OAuth2Claims = Depends(claims_dep),  # noqa: B008
        _rl: None = Depends(_registration_rate_limit),  # noqa: B008
    ) -> RegistrationReceipt:
        manifest_url_str = str(body.manifest_url)
        cache_key = manifest_url_cache_key(manifest_url_str)
        cache = getattr(request.app.state, "registration_receipt_cache", None)
        if isinstance(cache, MutableMapping) and cache_key in cache:
            return cast(RegistrationReceipt, cache[cache_key])

        agent_id = deterministic_registration_agent_id(manifest_url_str)

        timeout = httpx.Timeout(cfg.httpx_timeout)
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                manifest = await fetch_manifest_at_url(client, manifest_url_str)
            except WebhookURLValidationError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except httpx.HTTPStatusError as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"Failed to fetch manifest: HTTP {exc.response.status_code}",
                ) from exc
            except httpx.RequestError as exc:
                raise HTTPException(
                    status_code=502, detail=f"Manifest fetch failed: {exc}"
                ) from exc
            except ManifestValidationError as exc:
                raise HTTPException(status_code=400, detail=exc.message) from exc

        harness_url = harness_base_url_from_manifest(manifest)
        try:
            await validate_callback_url(harness_url, require_https=True)
        except WebhookURLValidationError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Harness base URL blocked: {exc}",
            ) from exc

        # Harness HTTP client does not follow redirects. This allowlist is
        # one-shot; following Location would skip SSRF checks (IMDS/link-local).
        report = await _run_harness(harness_url)
        if report.score < 1.0:
            failed = [c.model_dump(mode="json") for c in report.checks if not c.passed]
            raise HTTPException(
                status_code=422,
                detail=ComplianceFailedDetail(
                    score=report.score,
                    failed_checks=failed,
                    summary=report.summary,
                ).model_dump(mode="json"),
            )

        entry = _build_registry_entry(manifest, manifest_url_str)

        try:
            bot_result = await _open_pr(entry, manifest_url_str)
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception:
            logger.exception("registry.bot_pr_failed")
            raise HTTPException(
                status_code=502,
                detail="Pull request flow failed. Check server logs for details.",
            ) from None

        receipt = RegistrationReceipt(
            agent_id=agent_id,
            urn=manifest.id,
            harness_score=report.score,
            pr_url=bot_result.pr_url,
            status="queued",
            trust_level=TRUST_LEVEL_SELF_SIGNED,
        )
        if isinstance(cache, MutableMapping):
            cache[cache_key] = receipt
        return receipt

    return router


__all__ = [
    "AutoRegistrationConfig",
    "ComplianceFailedDetail",
    "REGISTRY_REGISTER_SCOPE",
    "RegisterAgentRequest",
    "RegistrationReceipt",
    "create_auto_registration_router",
    "deterministic_registration_agent_id",
    "fetch_manifest_at_url",
    "harness_base_url_from_manifest",
    "manifest_url_cache_key",
]
