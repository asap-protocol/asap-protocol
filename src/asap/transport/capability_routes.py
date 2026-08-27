"""Routes for ``/asap/capability/*`` and ``POST /asap/agent/reactivate``."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from asap.auth.agent_jwt import (
    HOST_REVOKED_ERROR,
    JwtVerifyResult,
    verify_agent_jwt,
)
from asap.auth.jti_replay_cache import JtiReplayCacheProtocol
from pydantic import ValidationError

from asap.auth.capabilities import CapabilityGrant, CapabilityRegistry
from asap.auth.identity import AgentStore, HostStore, reactivate_agent, save_agent_unless_revoked
from asap.models.base import ASAPBaseModel
from asap.observability import get_logger
from asap.transport._auth_helpers import bearer_token_from_request, verify_host_bearer
from asap.transport._state_deps import require_identity_limiter
from asap.transport.rate_limit import ASAPRateLimiter

logger = get_logger(__name__)


def http_error_request_id(request: Request) -> str:
    """Return ``request.state.request_id`` when set, else a new UUID string."""
    rid = getattr(request.state, "request_id", None)
    if rid is not None and rid != "":
        return str(rid)
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class CapabilityExecuteBody(ASAPBaseModel):
    """Body for ``POST /asap/capability/execute``."""

    capability: str
    arguments: dict[str, Any] | None = None


class AgentReactivateBody(ASAPBaseModel):
    """Body for ``POST /asap/agent/reactivate``."""

    agent_id: str


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


async def verify_agent_bearer(
    request: Request,
    *,
    jti_replay_cache: JtiReplayCacheProtocol | None = None,
) -> tuple[JwtVerifyResult | None, JSONResponse | None]:
    """Verify an Agent JWT Bearer token."""
    token = bearer_token_from_request(request)
    if not token:
        return None, JSONResponse(
            status_code=401,
            content={"detail": "Authentication required"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    host_store: HostStore = request.app.state.identity_host_store
    agent_store: AgentStore = request.app.state.identity_agent_store
    audience = request.app.state.identity_jwt_audience
    jti_cache = jti_replay_cache or request.app.state.identity_jti_cache
    result = await verify_agent_jwt(
        token,
        host_store,
        agent_store,
        expected_audience=audience,
        jti_replay_cache=jti_cache,
    )
    if not result.ok:
        status = (
            403 if result.error in ("agent_expired", "agent_revoked", HOST_REVOKED_ERROR) else 401
        )
        return None, JSONResponse(
            status_code=status,
            content={
                "detail": result.error,
                "request_id": http_error_request_id(request),
            },
        )
    return result, None


def _registry(request: Request) -> CapabilityRegistry:
    registry: CapabilityRegistry = request.app.state.capability_registry
    return registry


def _grant_to_dict(g: CapabilityGrant) -> dict[str, Any]:
    d: dict[str, Any] = {"capability": g.capability, "status": g.status}
    if g.constraints:
        d["constraints"] = g.constraints
    if g.granted_by:
        d["granted_by"] = g.granted_by
    if g.reason:
        d["reason"] = g.reason
    if g.expires_at:
        d["expires_at"] = g.expires_at.isoformat()
    return d


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def _handle_capability_list(
    request: Request,
    *,
    query: str = "",
    cursor: int = 0,
    limit: int = 100,
) -> JSONResponse:
    """List capabilities. Auth-mode aware: no-auth, Host JWT, or Agent JWT."""
    registry = _registry(request)
    definitions = registry.list_capabilities()

    query = query.lower()

    if query:
        definitions = [
            d for d in definitions if query in d.name.lower() or query in d.description.lower()
        ]

    page = definitions[cursor : cursor + limit]
    next_cursor = cursor + limit if cursor + limit < len(definitions) else None

    items: list[dict[str, Any]] = []
    # Try Agent JWT first, then Host JWT, then no-auth
    token = bearer_token_from_request(request)
    agent_id: str | None = None
    if token:
        host_store: HostStore = request.app.state.identity_host_store
        agent_store: AgentStore = request.app.state.identity_agent_store
        audience = request.app.state.identity_jwt_audience
        agent_result = await verify_agent_jwt(
            token,
            host_store,
            agent_store,
            expected_audience=audience,
        )
        if agent_result.ok and agent_result.agent:
            agent_id = agent_result.agent.agent_id

    for d in page:
        item: dict[str, Any] = {"name": d.name, "description": d.description}
        if d.location:
            item["location"] = d.location
        if agent_id:
            grants = registry.get_grants(agent_id)
            matching = [g for g in grants if g.capability == d.name]
            item["grant_status"] = matching[0].status if matching else None
        items.append(item)

    body: dict[str, Any] = {"capabilities": items}
    if next_cursor is not None:
        body["next_cursor"] = next_cursor
    return JSONResponse(status_code=200, content=body)


async def _handle_capability_describe(request: Request, name: str) -> JSONResponse:
    """Return full capability detail."""
    registry = _registry(request)
    defn = registry.describe(name)
    if defn is None:
        return JSONResponse(status_code=404, content={"detail": f"capability {name!r} not found"})

    body: dict[str, Any] = {"name": defn.name, "description": defn.description}
    if defn.input_schema:
        body["input_schema"] = defn.input_schema
    if defn.output_schema:
        body["output_schema"] = defn.output_schema
    if defn.location:
        body["location"] = defn.location
    return JSONResponse(status_code=200, content=body)


async def _handle_capability_execute(request: Request) -> JSONResponse:
    """Execute a capability (Agent JWT required)."""
    result, err = await verify_agent_bearer(request)
    if err is not None:
        return err
    if result is None or result.agent is None:
        return JSONResponse(status_code=401, content={"detail": "Invalid agent token"})

    try:
        raw_body = await request.json()
    except ValueError as e:
        return JSONResponse(status_code=400, content={"detail": f"Invalid JSON: {e}"})
    try:
        body = CapabilityExecuteBody.model_validate(raw_body)
    except ValidationError as e:
        return JSONResponse(status_code=400, content={"detail": e.errors()})

    registry = _registry(request)
    agent_id = result.agent.agent_id

    check = registry.check_grant(agent_id, body.capability, body.arguments)
    if not check.allowed:
        if check.violations:
            violations = [
                {"field": v.field, "operator": v.operator, "message": v.message}
                for v in check.violations
            ]
            return JSONResponse(
                status_code=403,
                content={
                    "error": "constraint_violated",
                    "capability": body.capability,
                    "violations": violations,
                    "request_id": http_error_request_id(request),
                },
            )
        return JSONResponse(
            status_code=403,
            content={
                "error": {
                    "code": "capability_not_granted",
                    "message": f"agent has no active grant for {body.capability!r}",
                    "data": {"required_capability": body.capability},
                    "request_id": http_error_request_id(request),
                },
            },
        )

    # Capability execution hook — callers wire actual logic via registry or handlers
    return JSONResponse(
        status_code=200,
        content={
            "capability": body.capability,
            "status": "executed",
            "agent_id": agent_id,
        },
    )


async def _handle_agent_reactivate(request: Request) -> JSONResponse:
    """Reactivate an expired agent (Host JWT required)."""
    jti_cache: JtiReplayCacheProtocol = request.app.state.identity_jti_cache
    result, err = await verify_host_bearer(request, jti_replay_cache=jti_cache)
    if err is not None:
        return err
    if result is None or result.claims is None:
        return JSONResponse(status_code=401, content={"detail": "Invalid host token"})

    try:
        raw_body = await request.json()
    except ValueError as e:
        return JSONResponse(status_code=400, content={"detail": f"Invalid JSON: {e}"})
    try:
        body = AgentReactivateBody.model_validate(raw_body)
    except ValidationError as e:
        return JSONResponse(status_code=400, content={"detail": e.errors()})

    agent_store: AgentStore = request.app.state.identity_agent_store
    host_store: HostStore = request.app.state.identity_host_store

    agent = await agent_store.get(body.agent_id)
    if agent is None:
        return JSONResponse(status_code=404, content={"detail": "unknown agent_id"})

    # Resolve host from JWT claims
    iss = result.claims.get("iss")
    if not isinstance(iss, str):
        return JSONResponse(status_code=400, content={"detail": "missing iss"})

    host = await host_store.get_by_public_key(iss)
    if host is None:
        return JSONResponse(status_code=404, content={"detail": "unknown host"})
    if host.host_id != agent.host_id:
        return JSONResponse(
            status_code=403, content={"detail": "agent does not belong to this host"}
        )

    # Re-read before mutate+save so concurrent revoke cannot be overwritten by a
    # stale reactivation snapshot (get→mutate→full-row save TOCTOU).
    fresh = await agent_store.get(body.agent_id)
    if fresh is None:
        return JSONResponse(status_code=404, content={"detail": "unknown agent_id"})
    try:
        reactivated = reactivate_agent(fresh, host)
    except ValueError as e:
        return JSONResponse(status_code=403, content={"detail": str(e)})
    try:
        await save_agent_unless_revoked(agent_store, reactivated)
    except ValueError:
        return JSONResponse(
            status_code=403,
            content={"detail": f"Agent {body.agent_id} is permanently revoked"},
        )

    # Capability decay: reset grants to host defaults
    registry = _registry(request)
    existing_grants = registry.get_grants(body.agent_id)
    default_caps = host.default_capabilities or []

    # Clear all current grants and re-grant only host defaults
    for g in existing_grants:
        registry.grant(
            body.agent_id,
            g.capability,
            status="denied",
            reason="capability decayed on reactivation",
        )
    for cap_name in default_caps:
        registry.grant(body.agent_id, cap_name, granted_by=host.host_id)

    grants = registry.get_grants(body.agent_id)

    logger.info(
        "asap.identity.agent_reactivate",
        action="reactivate",
        agent_id=body.agent_id,
        host_id=host.host_id,
    )
    return JSONResponse(
        status_code=200,
        content={
            "agent_id": reactivated.agent_id,
            "status": reactivated.status,
            "capabilities": [_grant_to_dict(g) for g in grants],
        },
    )


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def create_capability_router() -> APIRouter:
    """Return an :class:`APIRouter` with capability and reactivation endpoints."""
    router = APIRouter()

    @router.get("/asap/capability/list")
    async def capability_list(
        request: Request,
        query: Annotated[str, Query()] = "",
        cursor: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
        limiter: ASAPRateLimiter = Depends(require_identity_limiter),
    ) -> JSONResponse:
        limiter.check(request)
        return await _handle_capability_list(request, query=query, cursor=cursor, limit=limit)

    @router.get("/asap/capability/describe")
    async def capability_describe(
        request: Request,
        name: Annotated[str, Query(min_length=1)],
        limiter: ASAPRateLimiter = Depends(require_identity_limiter),
    ) -> JSONResponse:
        limiter.check(request)
        return await _handle_capability_describe(request, name)

    @router.post("/asap/capability/execute")
    async def capability_execute(
        request: Request,
        limiter: ASAPRateLimiter = Depends(require_identity_limiter),
    ) -> JSONResponse:
        limiter.check(request)
        return await _handle_capability_execute(request)

    @router.post("/asap/agent/reactivate")
    async def agent_reactivate(
        request: Request,
        limiter: ASAPRateLimiter = Depends(require_identity_limiter),
    ) -> JSONResponse:
        limiter.check(request)
        return await _handle_agent_reactivate(request)

    return router
