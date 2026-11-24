"""The reference A2A v1.0 gateway: the A2A flow of v2.6.0.

One FastAPI app, built by :func:`create_gateway_app`:

- ``create_app`` from ``asap-protocol`` serves the ASAP manifest and, beside
  it, the signed Agent Card from :func:`asap.adapters.a2a.build_signed_agent_card`
  (COMPAT-003). The card security states what the external-token config enforces.
- ``POST /a2a`` is the A2A JSON-RPC interface. A gate in front of the
  ``a2a-sdk`` dispatcher verifies the bearer token with
  :class:`asap.auth.external_verifier.ExternalTokenVerifier`, selects the skill,
  and checks the grant (``examples/a2a_gateway/skill_call.py``). Only then does
  the dispatcher run, and its executor runs exactly that decision.

The gateway never accepts an ASAP Agent JWT as an A2A credential (the
verifier refuses ``typ`` ``agent+jwt``), never sends the caller's token
anywhere, and removes it from what the ``a2a-sdk`` layer sees.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, cast

import httpx
from a2a.helpers import new_data_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler, build_error_response
from a2a.server.routes import ServerCallContextBuilder, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCard as SdkAgentCard
from a2a.types import SendMessageRequest
from a2a.utils.errors import A2AError, InvalidParamsError, UnsupportedOperationError
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from google.protobuf.json_format import ParseDict, ParseError

from asap.adapters.a2a import AgentCardInputs, CardSigner, build_signed_agent_card
from asap.auth.external_tokens import (
    ExternalPrincipal,
    ExternalTokenConfig,
    ExternalTokenError,
    ExternalTokenErrorReason,
)
from asap.auth.external_verifier import ExternalTokenVerifier
from asap.models.entities import Manifest
from asap.observability import get_logger
from asap.transport.server import create_app
from asap.utils.guarded_fetch import AddressResolver
from examples.a2a_gateway import refusals
from examples.a2a_gateway.demo_agent import A2A_PATH, SkillHandler
from examples.a2a_gateway.skill_call import (
    AuthorizedSkillCall,
    SkillSelectionError,
    check_skill_grant,
    select_skill_call,
)

logger = get_logger(__name__)

SKILL_CALL_STATE_KEY = "asap_skill_call"
# Never handed to the a2a-sdk layer: the executor and the skills do not see the token.
_HIDDEN_HEADERS = frozenset({"authorization", "cookie"})
Dispatch = Callable[[Request], Awaitable[Response]]


def create_gateway_app(
    manifest: Manifest,
    card_inputs: AgentCardInputs,
    *,
    signers: Sequence[CardSigner],
    external_tokens: ExternalTokenConfig,
    skills: Mapping[str, SkillHandler],
    dns_resolver: AddressResolver | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    """Return the gateway app for *manifest*, whose skills run as *skills*.

    *dns_resolver* and *transport* reach the token verifier's key fetches; they
    are for tests, as in :class:`asap.adapters.a2a.AgentCardIngestor`.

    Raises:
        ValueError: *skills* does not hold exactly the manifest's skills.
        asap.adapters.a2a.A2AAdapterError: The card cannot be built or signed.
    """
    skill_ids = [skill.id for skill in manifest.capabilities.skills]
    if sorted(skills) != sorted(skill_ids):
        raise ValueError(
            f"skills {sorted(skills)} must match the manifest skills {sorted(skill_ids)}"
        )
    card = build_signed_agent_card(
        manifest, card_inputs, signers=signers, external_tokens=external_tokens
    ).card
    app: FastAPI = create_app(manifest, agent_card=card)
    sdk_card = ParseDict(card.model_dump(mode="json"), SdkAgentCard())
    handler = DefaultRequestHandler(SkillCallExecutor(skills), InMemoryTaskStore(), sdk_card)
    [rpc_route] = create_jsonrpc_routes(
        handler, rpc_url=A2A_PATH, context_builder=SkillCallContextBuilder()
    )
    verifier = ExternalTokenVerifier(external_tokens, resolver=dns_resolver, transport=transport)
    gate = A2AGate(verifier, skill_ids, cast(Dispatch, rpc_route.endpoint))
    app.add_api_route(A2A_PATH, gate.handle, methods=["POST"], include_in_schema=False)
    return app


class A2AGate:
    """Token layer and skill decision in front of the ``a2a-sdk`` JSON-RPC dispatcher."""

    def __init__(
        self, verifier: ExternalTokenVerifier, skill_ids: Sequence[str], dispatch: Dispatch
    ) -> None:
        self._verifier = verifier
        self._skill_ids = list(skill_ids)
        self._dispatch = dispatch

    async def handle(self, request: Request) -> Response:
        """Authenticate, decide the skill call, then hand the request to the dispatcher."""
        principal = await self._authenticate(request.headers.get("authorization"))
        if isinstance(principal, Response):
            return principal
        body = await _json_body(request)
        if not isinstance(body, dict) or not isinstance(body.get("method"), str):
            # Not a JSON-RPC call: the dispatcher answers with its parse error.
            return await self._dispatch(request)
        if body["method"] != "SendMessage":
            only = UnsupportedOperationError(message="this gateway serves SendMessage only")
            return _rpc_error(body, only)
        decision = self._decide(principal, body)
        if isinstance(decision, Response):
            return decision
        setattr(request.state, SKILL_CALL_STATE_KEY, decision)
        return await self._dispatch(request)

    async def _authenticate(self, authorization: str | None) -> ExternalPrincipal | Response:
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            return refusals.auth_required()
        try:
            return await self._verifier.verify(token.strip())
        except ExternalTokenError as exc:
            # The typed reason goes to the log only, never the token; the answer
            # is the same for every reason, so it names no allowlisted issuer.
            logger.warning("a2a_gateway.token_rejected", reason=exc.reason.value)
            if exc.reason is ExternalTokenErrorReason.KEY_SOURCE_UNAVAILABLE:
                return refusals.auth_unavailable()
            return refusals.invalid_token()

    def _decide(
        self, principal: ExternalPrincipal, body: dict[str, Any]
    ) -> AuthorizedSkillCall | Response:
        try:
            params = ParseDict(body.get("params"), SendMessageRequest(), ignore_unknown_fields=True)
            skill_id, arguments = select_skill_call(params.message, self._skill_ids)
        except (ParseError, SkillSelectionError, TypeError, ValueError) as exc:
            return _rpc_error(
                body, InvalidParamsError(message=f"invalid SendMessage params: {exc}")
            )
        check = check_skill_grant(principal, skill_id, arguments)
        outcome = (
            "allowed" if check.allowed else "constraint_violation" if check.violations else "denied"
        )
        logger.info(
            "a2a_gateway.skill_call",
            issuer=principal.issuer,
            subject=principal.subject,
            skill_id=skill_id,
            outcome=outcome,
        )
        if check.violations:
            return refusals.constraint_violation(check.violations)
        if not check.allowed:
            return refusals.capability_denied()
        return AuthorizedSkillCall(principal=principal, skill_id=skill_id, arguments=arguments)


class SkillCallContextBuilder(ServerCallContextBuilder):
    """Build the ``a2a-sdk`` call context: the gate's decision, and no credential header."""

    def build(self, request: Request) -> ServerCallContext:
        """Keep ``A2A-Version`` (the SDK checks it); drop ``Authorization`` and cookies."""
        headers = {k: v for k, v in request.headers.items() if k not in _HIDDEN_HEADERS}
        state: dict[str, Any] = {"headers": headers}
        decision = getattr(request.state, SKILL_CALL_STATE_KEY, None)
        if isinstance(decision, AuthorizedSkillCall):
            state[SKILL_CALL_STATE_KEY] = decision
        return ServerCallContext(state=state)


class SkillCallExecutor(AgentExecutor):
    """Run the skill the gate authorized and answer with one A2A message (no task is kept)."""

    def __init__(self, skills: Mapping[str, SkillHandler]) -> None:
        self._skills = dict(skills)

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Run the authorized call; fail closed when the gate made no decision."""
        decision = context.call_context.state.get(SKILL_CALL_STATE_KEY)
        if not isinstance(decision, AuthorizedSkillCall):
            raise UnsupportedOperationError(message="no authorized skill call for this request")
        result = await self._skills[decision.skill_id](decision.arguments)
        reply = new_data_message(result, context_id=context.context_id, task_id=context.task_id)
        await event_queue.enqueue_event(reply)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """The gateway keeps no task, so there is nothing to cancel."""
        raise UnsupportedOperationError(message="this gateway keeps no task to cancel")


async def _json_body(request: Request) -> Any:
    try:
        return await request.json()
    except ValueError:
        return None


def _rpc_error(body: dict[str, Any], error: A2AError) -> JSONResponse:
    """A JSON-RPC error with HTTP 200, as the ``a2a-sdk`` dispatcher answers."""
    request_id = body.get("id")
    if isinstance(request_id, bool) or not isinstance(request_id, str | int):
        request_id = None
    return JSONResponse(build_error_response(request_id, error))
