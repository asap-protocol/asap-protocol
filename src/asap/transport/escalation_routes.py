"""HTTP routes for capability escalation (``POST /asap/agent/request-capability``)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import ConfigDict, Field, ValidationError

from asap.auth.approval import (
    ApprovalStore,
    create_ciba_approval,
    create_device_authorization,
    select_approval_method,
)
from asap.auth.capabilities import (
    CapabilityRegistry,
    partition_escalation_capability_specs,
)
from asap.auth.identity import HostIdentity
from asap.models.base import ASAPBaseModel
from asap.observability import get_logger
from asap.transport.agent_routes import (
    apply_capability_specs_to_registry,
    background_a2h_resolve,
    parse_capability_registration_body,
)
from asap.transport.capability_routes import verify_agent_bearer
from asap.transport._state_deps import require_identity_limiter
from asap.transport.rate_limit import ASAPRateLimiter

logger = get_logger(__name__)


class RequestCapabilityBody(ASAPBaseModel):
    """Body for ``POST /asap/agent/request-capability``."""

    model_config = ConfigDict(extra="forbid")

    capabilities: list[dict[str, Any]] = Field(
        ...,
        min_length=1,
        description="Requested capability specs: ``{name, constraints?}`` per item.",
    )


async def _handle_request_capability(
    request: Request,
    background_tasks: BackgroundTasks,
) -> JSONResponse:
    """Request additional capabilities for an active agent (Agent JWT)."""
    jti_cache = request.app.state.identity_jti_cache
    result, err = await verify_agent_bearer(request, jti_replay_cache=jti_cache)
    if err is not None:
        return err
    if result is None or result.agent is None or result.host is None:
        return JSONResponse(status_code=401, content={"detail": "Invalid agent token"})

    agent = result.agent
    host: HostIdentity = result.host
    if agent.status != "active":
        return JSONResponse(
            status_code=400,
            content={"detail": "agent must be active to request capability escalation"},
        )

    try:
        raw = await request.json()
    except (ValueError, UnicodeDecodeError) as e:
        return JSONResponse(status_code=400, content={"detail": f"Invalid JSON: {e}"})
    if not isinstance(raw, dict):
        return JSONResponse(status_code=400, content={"detail": "JSON body must be an object"})

    try:
        body = RequestCapabilityBody.model_validate(raw)
    except ValidationError as e:
        return JSONResponse(status_code=400, content={"detail": e.errors()})

    _names, capability_specs = parse_capability_registration_body(
        {"capabilities": body.capabilities},
    )
    if not _names:
        return JSONResponse(
            status_code=400,
            content={"detail": "each capability must include a non-empty name"},
        )

    registry: CapabilityRegistry | None = (
        request.app.state.capability_registry
        if hasattr(request.app.state, "capability_registry")
        else None
    )
    if registry is None:
        return JSONResponse(
            status_code=500, content={"detail": "capability registry not configured"}
        )

    needs_specs, auto_specs = partition_escalation_capability_specs(
        host,
        capability_specs,
        existing_grants=registry.get_grants(agent.agent_id),
    )
    host_id = host.host_id
    agent_id = agent.agent_id

    grant_payloads: list[dict[str, Any]] = apply_capability_specs_to_registry(
        registry,
        agent_id,
        host_id,
        auto_specs,
    )

    if not needs_specs:
        return JSONResponse(
            status_code=200,
            content={
                "agent_id": agent_id,
                "host_id": host_id,
                "status": "active",
                "agent_capability_grants": grant_payloads,
            },
        )

    approval_store: ApprovalStore | None = getattr(
        request.app.state,
        "identity_approval_store",
        None,
    )
    if approval_store is None:
        return JSONResponse(
            status_code=500,
            content={"detail": "approval store not configured"},
        )

    need_names = [str(s.get("name", "")) for s in needs_specs if isinstance(s.get("name"), str)]
    host_supports_ciba = bool(getattr(request.app.state, "identity_host_supports_ciba", True))
    method = select_approval_method(host, agent, host_supports_ciba=host_supports_ciba)
    if method == "ciba":
        approval_obj = await create_ciba_approval(
            approval_store,
            agent_id,
            need_names,
            capability_specs=needs_specs,
            approval_kind="escalation",
        )
    else:
        approval_obj = await create_device_authorization(
            approval_store,
            agent_id,
            need_names,
            capability_specs=needs_specs,
            approval_kind="escalation",
        )

    ch = getattr(request.app.state, "identity_approval_a2h_channel", None)
    if ch is not None:
        principal = host.user_id if host.user_id else host_id
        if not host.user_id:
            logger.warning(
                "asap.identity.escalation_a2h_principal_fallback",
                host_id=host_id,
                agent_id=agent_id,
            )
        background_tasks.add_task(
            background_a2h_resolve,
            ch,
            agent_id,
            context=f"ASAP capability escalation {agent_id} for host {host_id}",
            principal_id=str(principal),
        )

    logger.info(
        "asap.identity.request_capability",
        action="request_capability",
        agent_id=agent_id,
        host_id=host_id,
        pending_capabilities=need_names,
    )

    response: dict[str, Any] = {
        "agent_id": agent_id,
        "host_id": host_id,
        "status": "pending",
        "approval": approval_obj.model_dump(mode="json"),
    }
    if grant_payloads:
        response["agent_capability_grants"] = grant_payloads
    return JSONResponse(status_code=200, content=response)


def create_escalation_router() -> APIRouter:
    """Return routes for ``POST /asap/agent/request-capability``."""
    router = APIRouter()

    @router.post("/asap/agent/request-capability")
    async def request_capability_route(
        request: Request,
        background_tasks: BackgroundTasks,
        limiter: ASAPRateLimiter = Depends(require_identity_limiter),
    ) -> JSONResponse:
        """Request additional capabilities (Agent JWT; may start Device Auth / CIBA)."""
        limiter.check(request)
        return await _handle_request_capability(request, background_tasks)

    return router
