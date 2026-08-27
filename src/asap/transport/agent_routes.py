"""Agent identity HTTP endpoints (``/asap/agent/*``).

Provides a FastAPI :class:`APIRouter` for agent registration, status queries,
revocation, and key rotation.  All endpoints authenticate via Host JWT
(Bearer) and use a dedicated rate limiter stored on ``app.state.identity_limiter``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, cast

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from fastapi.responses import JSONResponse
from joserfc.errors import JoseError
from joserfc.jwk import OKPKey
from pydantic import Field

from asap.auth.agent_jwt import (
    AGENT_PUBLIC_KEY_CLAIM,
    HOST_PUBLIC_KEY_CLAIM,
    JwtVerifyResult,
)
from asap.auth.jti_replay_cache import JtiReplayCacheProtocol
from asap.auth.approval import (
    A2HApprovalChannel,
    ApprovalMethod,
    ApprovalRequestState,
    ApprovalStore,
    approval_object_for_client,
    create_ciba_approval,
    create_device_authorization,
    select_approval_method,
)
from asap.auth.self_auth import (
    FreshSessionConfig,
    WebAuthnVerifier,
    check_webauthn_for_approval_path,
    default_webauthn_verifier,
    fresh_session_violation_detail,
)
from asap.auth.capabilities import CapabilityRegistry, escalation_requires_user_consent
from asap.auth.identity import (
    AgentSession,
    AgentStore,
    HostIdentity,
    HostStore,
    RevokedAgentOverwriteError,
    host_urn_from_thumbprint,
    jwk_thumbprint_sha256,
    save_agent_unless_revoked,
)
from asap.models.base import ASAPBaseModel
from asap.models.ids import generate_id
from asap.observability import get_logger
from asap.transport._auth_helpers import verify_host_bearer
from asap.transport._state_deps import require_identity_limiter
from asap.transport.capability_routes import _grant_to_dict
from asap.transport.rate_limit import ASAPRateLimiter

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class AgentRevokeBody(ASAPBaseModel):
    """Body for ``POST /asap/agent/revoke``."""

    agent_id: str = Field(..., min_length=1)


class AgentRotateKeyBody(ASAPBaseModel):
    """Body for ``POST /asap/agent/rotate-key``."""

    agent_id: str = Field(..., min_length=1)
    new_public_key: dict[str, Any]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def parse_capability_registration_body(
    raw_body: dict[str, Any],
) -> tuple[list[str], list[dict[str, Any]]]:
    """Extract capability names and raw spec dicts from a register JSON body."""
    specs: list[dict[str, Any]] = []
    names: list[str] = []
    requested = raw_body.get("capabilities")
    if not isinstance(requested, list):
        return names, specs
    for cap_req in requested:
        if isinstance(cap_req, str):
            if cap_req.strip():
                names.append(cap_req)
                specs.append({"name": cap_req})
        elif isinstance(cap_req, dict):
            cap_name = cap_req.get("name", "")
            cap_name_str = cap_name if isinstance(cap_name, str) else ""
            if cap_name_str:
                names.append(cap_name_str)
                specs.append(dict(cap_req))
    return names, specs


def _parse_agent_controls_browser(raw_body: dict[str, Any]) -> bool:
    """Whether the client declares it controls the browser (JSON ``true`` only)."""
    return raw_body.get("agent_controls_browser") is True


def _identity_fresh_session_config(request: Request) -> FreshSessionConfig | None:
    return getattr(request.app.state, "identity_fresh_session_config", None)


def _approval_fresh_session_response(
    request: Request,
    claims: dict[str, Any],
) -> JSONResponse | None:
    """403 when ``identity_fresh_session_config`` is set and Host JWT ``iat`` is too old."""
    cfg = _identity_fresh_session_config(request)
    if cfg is None:
        return None
    detail = fresh_session_violation_detail(claims, cfg)
    if detail is None:
        return None
    return JSONResponse(status_code=403, content={"detail": detail})


def _webauthn_verifier(request: Request) -> WebAuthnVerifier:
    """Verifier from ``app.state``, or :func:`default_webauthn_verifier` if unset (logs warning)."""
    configured = getattr(request.app.state, "identity_webauthn_verifier", None)
    if configured is not None:
        return cast("WebAuthnVerifier", configured)
    logger.warning(
        "asap.identity.webauthn_verifier_fallback",
        detail="app.state.identity_webauthn_verifier unset; using default_webauthn_verifier()",
    )
    return default_webauthn_verifier()


async def _approval_webauthn_response(
    request: Request,
    raw_body: dict[str, Any],
    requested_names: list[str],
    host_id: str,
    *,
    agent_controls_browser: bool,
) -> JSONResponse | None:
    """Reject registration when WebAuthn is required but missing or invalid."""
    cfg = _identity_fresh_session_config(request)
    result = await check_webauthn_for_approval_path(
        requested_names,
        raw_body,
        cfg,
        _webauthn_verifier(request),
        host_id=host_id,
        agent_controls_browser=agent_controls_browser,
    )
    if result.detail is None:
        return None
    if result.http_status == 403:
        return JSONResponse(status_code=403, content={"detail": "webauthn_required"})
    return JSONResponse(status_code=400, content={"detail": result.detail})


def _needs_registration_approval(host: HostIdentity, requested_names: list[str]) -> bool:
    """Hosts that are not yet active, or requests outside default caps, need approval."""
    return escalation_requires_user_consent(host, requested_names)


def apply_capability_specs_to_registry(
    registry: CapabilityRegistry,
    agent_id: str,
    host_id: str,
    capability_specs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Mirror of register-time capability grants (used after approval or auto-approve)."""
    capability_grants: list[dict[str, Any]] = []
    for cap_req in capability_specs:
        cap_name_raw = cap_req.get("name", "")
        cap_name = cap_name_raw if isinstance(cap_name_raw, str) else ""
        if not cap_name:
            continue
        constraints = cap_req.get("constraints") if isinstance(cap_req, dict) else None
        defn = registry.describe(cap_name)
        if defn is not None:
            g = registry.grant(
                agent_id,
                cap_name,
                constraints=constraints,
                granted_by=host_id,
            )
            capability_grants.append(
                {
                    "capability": g.capability,
                    "status": g.status,
                }
            )
        else:
            g = registry.grant(
                agent_id,
                cap_name,
                status="denied",
                reason=f"capability {cap_name!r} not found",
                granted_by=host_id,
            )
            capability_grants.append(
                {
                    "capability": g.capability,
                    "status": g.status,
                    "reason": g.reason,
                }
            )
    return capability_grants


def _grants_for_agent(registry: CapabilityRegistry, agent_id: str) -> list[dict[str, Any]]:
    return [_grant_to_dict(g) for g in registry.get_grants(agent_id)]


async def background_a2h_resolve(
    channel: A2HApprovalChannel,
    agent_id: str,
    *,
    context: str,
    principal_id: str,
) -> None:
    try:
        await channel.resolve_via_a2h(agent_id, context=context, principal_id=principal_id)
    except Exception:
        logger.exception(
            "asap.identity.a2h_resolve_failed",
            agent_id=agent_id,
            principal_id=principal_id,
        )


def _effective_identity_host_id(result: JwtVerifyResult) -> str:
    """Host id from store or synthetic id for first-seen keys (matches register)."""
    claims = result.claims
    if claims is None:
        msg = "verified host JWT must include claims"
        raise ValueError(msg)
    iss = claims.get("iss")
    if not isinstance(iss, str):
        msg = "verified host JWT must include iss"
        raise ValueError(msg)
    if result.host is not None:
        return result.host.host_id
    return host_urn_from_thumbprint(iss)


def _agent_lifecycle_json(session: AgentSession) -> dict[str, Any]:
    """Serialize agent lifecycle fields for JSON responses."""

    def _td_seconds(td: timedelta | None) -> float | None:
        if td is None:
            return None
        return td.total_seconds()

    return {
        "mode": session.mode,
        "session_ttl": _td_seconds(session.session_ttl),
        "max_lifetime": _td_seconds(session.max_lifetime),
        "absolute_lifetime": _td_seconds(session.absolute_lifetime),
        "created_at": session.created_at.isoformat(),
        "activated_at": session.activated_at.isoformat() if session.activated_at else None,
        "last_used_at": session.last_used_at.isoformat() if session.last_used_at else None,
    }


async def _persist_pending_approval_activation(
    agent_store: AgentStore,
    agent_id: str,
    *,
    registry: CapabilityRegistry | None,
    host_id: str,
    capability_specs: list[dict[str, Any]] | None,
) -> None:
    """Activate a pending agent after approval; skip write if concurrently revoked."""
    fresh = await agent_store.get(agent_id)
    if fresh is None or fresh.status != "pending":
        return
    activated = fresh.model_copy(
        update={
            "status": "active",
            "activated_at": datetime.now(timezone.utc),
        },
    )
    try:
        await save_agent_unless_revoked(agent_store, activated)
    except RevokedAgentOverwriteError:
        return
    if registry is not None and capability_specs:
        apply_capability_specs_to_registry(registry, agent_id, host_id, capability_specs)


async def _persist_pending_approval_rejection(agent_store: AgentStore, agent_id: str) -> None:
    """Mark a pending agent rejected; skip write if concurrently revoked."""
    fresh = await agent_store.get(agent_id)
    if fresh is None or fresh.status != "pending":
        return
    rejected = fresh.model_copy(update={"status": "rejected"})
    try:
        await save_agent_unless_revoked(agent_store, rejected)
    except RevokedAgentOverwriteError:
        return


# ---------------------------------------------------------------------------
# Handler implementations
# ---------------------------------------------------------------------------


async def _handle_agent_register(
    request: Request,
    background_tasks: BackgroundTasks,
) -> JSONResponse:
    """Create or return an agent session from a verified Host JWT."""
    jti_cache: JtiReplayCacheProtocol = request.app.state.identity_jti_cache
    result, err = await verify_host_bearer(request, jti_replay_cache=jti_cache)
    if err is not None:
        return err
    if result is None:
        return JSONResponse(status_code=401, content={"detail": "Invalid host token"})

    agent_store: AgentStore = request.app.state.identity_agent_store
    host_store: HostStore = request.app.state.identity_host_store
    claims = result.claims
    if claims is None:
        return JSONResponse(status_code=401, content={"detail": "Invalid host token"})

    agent_pub_raw = claims.get(AGENT_PUBLIC_KEY_CLAIM)
    if not isinstance(agent_pub_raw, dict):
        return JSONResponse(
            status_code=400,
            content={"detail": "missing or invalid agent_public_key claim in host JWT"},
        )

    try:
        OKPKey.import_key(cast("dict[str, str | list[str]]", dict(agent_pub_raw)))
    except (JoseError, TypeError, ValueError):
        return JSONResponse(status_code=400, content={"detail": "invalid agent_public_key JWK"})

    agent_pub: dict[str, Any] = dict(agent_pub_raw)

    host_pub = claims.get(HOST_PUBLIC_KEY_CLAIM)
    if not isinstance(host_pub, dict):
        return JSONResponse(
            status_code=400, content={"detail": "missing host_public_key in host JWT"}
        )

    host = result.host
    now = datetime.now(timezone.utc)
    iss = claims.get("iss")
    if not isinstance(iss, str) or not iss.strip():
        return JSONResponse(status_code=400, content={"detail": "missing iss in host JWT"})
    if host is None:
        host = HostIdentity(
            host_id=host_urn_from_thumbprint(iss),
            public_key=dict(host_pub),
            status="pending",
            created_at=now,
            updated_at=now,
        )
        await host_store.save(host)

    host_id = host.host_id

    agent_tp = jwk_thumbprint_sha256(agent_pub)
    existing: AgentSession | None = None
    for sess in await agent_store.list_by_host(host_id):
        if jwk_thumbprint_sha256(sess.public_key) == agent_tp:
            existing = sess
            break

    if existing is not None:
        logger.info(
            "asap.identity.agent_register",
            action="register_idempotent",
            agent_id=existing.agent_id,
            host_id=existing.host_id,
        )
        return JSONResponse(
            status_code=200,
            content={
                "agent_id": existing.agent_id,
                "host_id": existing.host_id,
                "status": existing.status,
            },
        )

    try:
        raw_body = await request.json()
    except (ValueError, UnicodeDecodeError):
        raw_body = {}
    if not isinstance(raw_body, dict):
        raw_body = {}

    requested_names, capability_specs = parse_capability_registration_body(raw_body)
    preferred_raw = raw_body.get("approval_method")
    preferred_method: ApprovalMethod | None = None
    if preferred_raw == "device_authorization":
        preferred_method = "device_authorization"
    elif preferred_raw == "ciba":
        preferred_method = "ciba"
    agent_controls_browser = _parse_agent_controls_browser(raw_body)

    needs = _needs_registration_approval(host, requested_names)
    approval_store: ApprovalStore | None = getattr(
        request.app.state,
        "identity_approval_store",
        None,
    )
    if needs and approval_store is None:
        return JSONResponse(
            status_code=500,
            content={"detail": "approval store not configured"},
        )

    registry: CapabilityRegistry | None = (
        request.app.state.capability_registry
        if hasattr(request.app.state, "capability_registry")
        else None
    )

    if needs and approval_store is not None:
        fresh_err = _approval_fresh_session_response(request, claims)
        if fresh_err is not None:
            return fresh_err
        wa_err = await _approval_webauthn_response(
            request,
            raw_body,
            requested_names,
            host_id,
            agent_controls_browser=agent_controls_browser,
        )
        if wa_err is not None:
            return wa_err

    agent_id = generate_id()
    session = AgentSession(
        agent_id=agent_id,
        host_id=host_id,
        public_key=agent_pub,
        mode="delegated",
        status="pending",
        created_at=now,
    )
    await agent_store.save(session)

    if not needs:
        activated = session.model_copy(update={"status": "active", "activated_at": now})
        try:
            await agent_store.save(activated)
        except RevokedAgentOverwriteError:
            return JSONResponse(
                status_code=400,
                content={"detail": f"cannot activate revoked agent {agent_id!r}"},
            )
        capability_grants: list[dict[str, Any]] = []
        if registry is not None and capability_specs:
            capability_grants = apply_capability_specs_to_registry(
                registry,
                agent_id,
                host_id,
                capability_specs,
            )
        logger.info(
            "asap.identity.agent_register",
            action="register",
            agent_id=agent_id,
            host_id=host_id,
        )
        response_content: dict[str, Any] = {
            "agent_id": agent_id,
            "host_id": host_id,
            "status": "active",
        }
        if capability_grants:
            response_content["agent_capability_grants"] = capability_grants
        return JSONResponse(status_code=200, content=response_content)

    if approval_store is None:
        return JSONResponse(
            status_code=500,
            content={"detail": "approval store not configured"},
        )
    host_supports_ciba = bool(getattr(request.app.state, "identity_host_supports_ciba", True))
    method = select_approval_method(
        host,
        session,
        host_supports_ciba=host_supports_ciba,
        preferred_method=preferred_method,
        agent_controls_browser=agent_controls_browser,
    )
    if method == "ciba":
        approval_obj = await create_ciba_approval(
            approval_store,
            agent_id,
            requested_names,
            capability_specs=capability_specs,
        )
    else:
        approval_obj = await create_device_authorization(
            approval_store,
            agent_id,
            requested_names,
            capability_specs=capability_specs,
        )

    ch = getattr(request.app.state, "identity_approval_a2h_channel", None)
    if ch is not None:
        principal = host.user_id if host.user_id else host_id
        if not host.user_id:
            logger.warning(
                "asap.identity.a2h_principal_fallback",
                host_id=host_id,
                agent_id=agent_id,
            )
        background_tasks.add_task(
            background_a2h_resolve,
            ch,
            agent_id,
            context=f"ASAP agent registration {agent_id} for host {host_id}",
            principal_id=principal,
        )

    logger.info(
        "asap.identity.agent_register",
        action="register",
        agent_id=agent_id,
        host_id=host_id,
    )
    return JSONResponse(
        status_code=200,
        content={
            "agent_id": agent_id,
            "host_id": host_id,
            "status": "pending",
            "approval": approval_obj.model_dump(mode="json"),
        },
    )


async def _handle_agent_status(request: Request, agent_id: str) -> JSONResponse:
    """Return agent session status and lifecycle for the authenticated host."""
    jti_cache: JtiReplayCacheProtocol = request.app.state.identity_jti_cache
    # Status polling may legitimately reuse the same Host JWT, but issue #249
    # still requires rejecting tokens already consumed on recording routes.
    result, err = await verify_host_bearer(
        request,
        jti_replay_cache=jti_cache,
        record_jti=False,
    )
    if err is not None:
        return err
    if result is None:
        return JSONResponse(status_code=401, content={"detail": "Invalid host token"})

    host_id = _effective_identity_host_id(result)
    agent_store: AgentStore = request.app.state.identity_agent_store
    session = await agent_store.get(agent_id)
    if session is None:
        return JSONResponse(status_code=404, content={"detail": "unknown agent_id"})
    if session.host_id != host_id:
        return JSONResponse(
            status_code=403,
            content={"detail": "agent does not belong to this host"},
        )

    approval_store: ApprovalStore | None = getattr(
        request.app.state,
        "identity_approval_store",
        None,
    )
    registry: CapabilityRegistry | None = (
        request.app.state.capability_registry
        if hasattr(request.app.state, "capability_registry")
        else None
    )

    appr: ApprovalRequestState | None = None
    if approval_store is not None:
        appr = await approval_store.get(agent_id)

    claims_status = result.claims
    if (
        session.status == "pending"
        and appr is not None
        and appr.status == "pending"
        and claims_status is not None
    ):
        fresh_poll = _approval_fresh_session_response(request, claims_status)
        if fresh_poll is not None:
            return fresh_poll

    if (
        session.status == "active"
        and appr is not None
        and appr.approval_kind == "escalation"
        and approval_store is not None
    ):
        if appr.status == "approved":
            if registry is not None and appr.capability_specs:
                apply_capability_specs_to_registry(
                    registry,
                    agent_id,
                    host_id,
                    appr.capability_specs,
                )
            await approval_store.remove(agent_id)
            appr = None
        elif appr.status in ("denied", "expired"):
            await approval_store.remove(agent_id)
            appr = None

    if session.status == "pending" and appr is not None:
        # Re-read before pending→active/rejected so concurrent revoke cannot be
        # overwritten by a stale full-row save (same class as LIFE-005 TOCTOU).
        if appr.status == "approved":
            await _persist_pending_approval_activation(
                agent_store,
                agent_id,
                registry=registry,
                host_id=host_id,
                capability_specs=appr.capability_specs,
            )
        elif appr.status == "denied":
            await _persist_pending_approval_rejection(agent_store, agent_id)

    refreshed = await agent_store.get(agent_id)
    if refreshed is not None:
        session = refreshed

    caps_out: list[dict[str, Any]] = []
    if registry is not None and session.status == "active":
        caps_out = _grants_for_agent(registry, agent_id)

    content: dict[str, Any] = {
        "agent_id": session.agent_id,
        "host_id": session.host_id,
        "status": session.status,
        "capabilities": caps_out,
        "agent_capability_grants": list(caps_out),
        "lifecycle": _agent_lifecycle_json(session),
    }
    if appr is not None:
        content["approval_status"] = appr.status
        if (session.status == "pending" and appr.status in ("pending", "expired")) or (
            session.status == "active"
            and appr.approval_kind == "escalation"
            and appr.status == "pending"
        ):
            base = approval_object_for_client(appr).model_dump(mode="json")
            content["approval"] = {**base, "state": appr.status}
        elif session.status == "rejected" and appr.deny_reason:
            content["deny_reason"] = appr.deny_reason
    return JSONResponse(status_code=200, content=content)


async def _handle_agent_revoke(request: Request, body: AgentRevokeBody) -> JSONResponse:
    """Permanently revoke an agent session for the authenticated host."""
    jti_cache: JtiReplayCacheProtocol = request.app.state.identity_jti_cache
    result, err = await verify_host_bearer(request, jti_replay_cache=jti_cache)
    if err is not None:
        return err
    if result is None:
        return JSONResponse(status_code=401, content={"detail": "Invalid host token"})

    host_id = _effective_identity_host_id(result)
    agent_store: AgentStore = request.app.state.identity_agent_store
    session = await agent_store.get(body.agent_id)
    if session is None:
        return JSONResponse(status_code=404, content={"detail": "unknown agent_id"})
    if session.host_id != host_id:
        return JSONResponse(
            status_code=403,
            content={"detail": "agent does not belong to this host"},
        )

    await agent_store.revoke(body.agent_id)
    logger.info(
        "asap.identity.agent_revoke",
        action="revoke",
        agent_id=body.agent_id,
        host_id=host_id,
    )
    return JSONResponse(
        status_code=200,
        content={"agent_id": body.agent_id, "status": "revoked"},
    )


async def _handle_agent_rotate_key(request: Request, body: AgentRotateKeyBody) -> JSONResponse:
    """Replace the agent session's Ed25519 public JWK (old JWTs no longer verify)."""
    jti_cache: JtiReplayCacheProtocol = request.app.state.identity_jti_cache
    result, err = await verify_host_bearer(request, jti_replay_cache=jti_cache)
    if err is not None:
        return err
    if result is None:
        return JSONResponse(status_code=401, content={"detail": "Invalid host token"})

    host_id = _effective_identity_host_id(result)
    agent_store: AgentStore = request.app.state.identity_agent_store
    session = await agent_store.get(body.agent_id)
    if session is None:
        return JSONResponse(status_code=404, content={"detail": "unknown agent_id"})
    if session.host_id != host_id:
        return JSONResponse(
            status_code=403,
            content={"detail": "agent does not belong to this host"},
        )
    if session.status == "revoked":
        return JSONResponse(
            status_code=400,
            content={"detail": "cannot rotate key for revoked agent"},
        )

    try:
        OKPKey.import_key(cast("dict[str, str | list[str]]", dict(body.new_public_key)))
    except (JoseError, TypeError, ValueError):
        return JSONResponse(
            status_code=400,
            content={"detail": "invalid new_public_key JWK"},
        )
    new_pub: dict[str, Any] = dict(body.new_public_key)

    new_tp = jwk_thumbprint_sha256(new_pub)
    if new_tp == jwk_thumbprint_sha256(session.public_key):
        return JSONResponse(
            status_code=200,
            content={"agent_id": session.agent_id, "status": session.status},
        )

    for other in await agent_store.list_by_host(host_id):
        if other.agent_id != session.agent_id and jwk_thumbprint_sha256(other.public_key) == new_tp:
            return JSONResponse(
                status_code=409,
                content={"detail": "another agent under this host already uses this public key"},
            )

    # Re-read before save so concurrent revoke cannot be overwritten by a stale
    # active/pending snapshot that still carries the new public key.
    fresh = await agent_store.get(body.agent_id)
    if fresh is None:
        return JSONResponse(status_code=404, content={"detail": "unknown agent_id"})
    if fresh.status == "revoked":
        return JSONResponse(
            status_code=400,
            content={"detail": "cannot rotate key for revoked agent"},
        )

    rotated = fresh.model_copy(update={"public_key": new_pub})
    try:
        await save_agent_unless_revoked(agent_store, rotated)
    except RevokedAgentOverwriteError:
        return JSONResponse(
            status_code=400,
            content={"detail": "cannot rotate key for revoked agent"},
        )
    logger.info(
        "asap.identity.agent_rotate_key",
        action="rotate_key",
        agent_id=rotated.agent_id,
        host_id=host_id,
    )
    return JSONResponse(
        status_code=200,
        content={"agent_id": rotated.agent_id, "status": rotated.status},
    )


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def create_agent_identity_router() -> APIRouter:
    """Return an :class:`APIRouter` with ``/asap/agent/*`` identity endpoints.

    The router reads identity stores, JTI replay cache, and the dedicated
    ``identity_limiter`` from ``request.app.state``.
    """
    router = APIRouter()

    @router.post("/asap/agent/register")
    async def agent_register(
        request: Request,
        background_tasks: BackgroundTasks,
        limiter: ASAPRateLimiter = Depends(require_identity_limiter),
    ) -> JSONResponse:
        """Register an agent session under a host using a Host JWT (Bearer)."""
        limiter.check(request)
        return await _handle_agent_register(request, background_tasks)

    @router.get("/asap/agent/status")
    async def agent_status(
        request: Request,
        agent_id: Annotated[str, Query(min_length=1)],
        limiter: ASAPRateLimiter = Depends(require_identity_limiter),
    ) -> JSONResponse:
        """Return agent status and lifecycle for the authenticated host (Host JWT)."""
        limiter.check(request)
        return await _handle_agent_status(request, agent_id)

    @router.post("/asap/agent/revoke")
    async def agent_revoke(
        request: Request,
        body: AgentRevokeBody,
        limiter: ASAPRateLimiter = Depends(require_identity_limiter),
    ) -> JSONResponse:
        """Revoke an agent session (Host JWT; body: ``agent_id``)."""
        limiter.check(request)
        return await _handle_agent_revoke(request, body)

    @router.post("/asap/agent/rotate-key")
    async def agent_rotate_key(
        request: Request,
        body: AgentRotateKeyBody,
        limiter: ASAPRateLimiter = Depends(require_identity_limiter),
    ) -> JSONResponse:
        """Rotate agent Ed25519 public key (Host JWT; body: ``agent_id``, ``new_public_key``)."""
        limiter.check(request)
        return await _handle_agent_rotate_key(request, body)

    return router
