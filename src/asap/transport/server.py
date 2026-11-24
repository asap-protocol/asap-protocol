"""FastAPI server implementation for ASAP protocol.

This module provides a production-ready FastAPI server that:
- Exposes POST /asap endpoint for JSON-RPC 2.0 wrapped ASAP messages
- Exposes POST /asap/stream for JSON-RPC input and SSE (``text/event-stream``) chunk responses
- Exposes GET /.well-known/asap/manifest.json for agent discovery
- Exposes GET /asap/metrics for Prometheus-compatible metrics
- Handles errors with proper JSON-RPC error responses
- Validates all incoming requests against ASAP schemas
- Uses HandlerRegistry for extensible payload processing
- Provides structured logging for observability
- Supports authentication based on manifest configuration

Example:
    >>> from asap.models.entities import Manifest, Capability, Endpoint, Skill, AuthScheme
    >>> from asap.transport.server import create_app
    >>> from asap.transport.handlers import HandlerRegistry
    >>>
    >>> manifest = Manifest(
    ...     id="urn:asap:agent:my-agent",
    ...     name="My Agent",
    ...     version="1.0.0",
    ...     description="Example agent",
    ...     capabilities=Capability(
    ...         asap_version="0.1",
    ...         skills=[Skill(id="echo", description="Echo skill")],
    ...         state_persistence=False
    ...     ),
    ...     endpoints=Endpoint(asap="http://localhost:8000/asap"),
    ...     auth=AuthScheme(schemes=["bearer"])  # Optional authentication
    ... )
    >>>
    >>> # Create app with default registry
    >>> app = create_app(manifest)
    >>>
    >>> # Or with custom registry and auth
    >>> registry = HandlerRegistry()
    >>> registry.register("task.request", my_custom_handler)
    >>> app = create_app(manifest, registry)
    >>>
    >>> # Run with: uvicorn asap.transport.server:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import importlib
import os
import sys
import threading
import time
from collections.abc import AsyncIterator  # noqa: F401  (re-exported for compat)
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from fastapi import FastAPI, WebSocket
from opentelemetry import context  # noqa: F401  (re-exported for compat)

from asap.models.constants import MAX_REQUEST_SIZE
from asap.models.entities import Capability, Endpoint, Manifest, Skill
from asap.models.envelope import Envelope
from asap.observability import get_logger, is_debug_mode

# ``is_debug_log_mode`` is imported (and kept) solely so tests can patch
# ``asap.transport.server.is_debug_log_mode``; the handler reads it via
# ``_server.is_debug_log_mode`` (attribute lookup) to stay patchable across
# the ``_request_handler`` module boundary. Re-exported explicitly for mypy
# ``no_implicit_reexport`` and for ``_request_handler``'s ``_server`` access.
from asap.observability import is_debug_log_mode as is_debug_log_mode
from asap.observability.tracing import configure_tracing
from asap.auth import JWKSValidator, OAuth2Config, OAuth2Middleware
from asap.auth.middleware import OPERATOR_API_PATH_PREFIXES
from asap.auth.agent_jwt import JtiReplayCache
from asap.auth.external_tokens import ExternalTokenConfig
from asap.auth.external_verifier import ExternalTokenVerifier
from asap.auth.jti_replay_cache import JtiReplayCacheProtocol
from asap.auth.approval import A2HApprovalChannel, ApprovalStore, InMemoryApprovalStore
from asap.auth.self_auth import (
    FreshSessionConfig,
    WebAuthnVerifier,
    default_webauthn_verifier,
)
from asap.auth.identity import (
    AgentStore,
    HostStore,
    InMemoryAgentStore,
    InMemoryHostStore,
)
from asap.transport.middleware import (
    ASAPVersionMiddleware,
    AuthenticationMiddleware,
    BearerTokenValidator,
    SizeLimitMiddleware,
    _get_sender_from_envelope,
    rate_limit_handler,
)
from asap.transport.rate_limit import (
    RateLimitExceeded,
    create_limiter,
    create_registration_rate_limiter,
)
from asap.observability.metrics import MetricsCollector
from asap.transport.executors import BoundedExecutor
from asap.transport.handlers import HandlerRegistry, create_default_registry
from asap.transport.jsonrpc import DEFAULT_MAX_BATCH_SIZE, JsonRpcRequest
from asap.state.metering import MeteringStore
from asap.state.snapshot import SnapshotStore
from asap.economics.audit import AuditStore
from asap.economics.sla_storage import SLAStorage
from asap.economics.storage import MeteringStorage
from asap.state.metering import MeteringStorageBridge
from asap.transport._external_auth import warn_if_oauth2_idp_unbound
from asap.transport.agent_routes import create_agent_identity_router
from asap.transport.capability_routes import CAPABILITY_EXECUTE_PATH, create_capability_router
from asap.transport.escalation_routes import create_escalation_router
from asap.transport.delegation_api import create_delegation_router
from asap.transport.sla_api import create_sla_router
from asap.transport.usage_api import create_usage_router
from asap.transport.routes import (
    create_audit_router,
    create_health_router,
    create_jsonrpc_router,
    create_websocket_router,
)
from asap.transport.routes.agent_card import (
    create_agent_card_router,
    validate_served_agent_card,
)
from asap.state.stores import create_snapshot_store
from asap.transport.validators import InMemoryNonceStore, NonceStore
from asap.transport.ws import WS_CLOSE_GOING_AWAY, WS_CLOSE_REASON_SHUTDOWN
from asap.transport.mtls import MTLSConfig

if TYPE_CHECKING:
    from asap.adapters.a2a import AgentCard
    from asap.registry.auto_registration import AutoRegistrationConfig

# Module logger
logger = get_logger(__name__)

# Environment variable to enable handler hot reload (development)
ENV_HOT_RELOAD = "ASAP_HOT_RELOAD"


class RegistryHolder:
    """Mutable holder for HandlerRegistry to support hot reload.

    When hot reload is enabled, a background thread watches handlers.py and
    replaces the registry on file change so new handler code is used without
    restarting the server.
    """

    def __init__(self, registry: HandlerRegistry) -> None:
        self.registry = registry
        self._executor: BoundedExecutor | None = None

    def replace_registry(self, new_registry: HandlerRegistry) -> None:
        if self._executor is not None:
            new_registry._executor = self._executor
        self.registry = new_registry


_HOT_RELOAD_RETRY_DELAY_SECONDS = 5.0


def _run_handler_watcher(holder: RegistryHolder, handlers_path: str) -> None:
    """Background thread: watch handlers_path and reload registry on change.

    On filesystem/watch errors the loop retries after a delay so the thread
    keeps running. If watchfiles is not installed, hot reload is skipped.
    """
    try:
        from watchfiles import watch
    except ImportError:
        logger.warning(
            "asap.server.handler_watcher_skip",
            path=handlers_path,
            message="watchfiles not installed; hot reload disabled. Install with: pip install watchfiles",
        )
        return
    while True:
        try:
            for changes in watch(handlers_path):
                if not changes:
                    continue
                try:
                    import asap.transport.handlers as handlers_module

                    importlib.reload(handlers_module)
                    new_registry = handlers_module.create_default_registry()
                    holder.replace_registry(new_registry)
                    logger.info(
                        "asap.server.handlers_reloaded",
                        path=handlers_path,
                        handlers=new_registry.list_handlers(),
                    )
                except Exception as e:
                    logger.warning(
                        "asap.server.handlers_reload_failed",
                        path=handlers_path,
                        error=str(e),
                    )
        except Exception as e:
            logger.warning(
                "asap.server.handler_watcher_retry",
                path=handlers_path,
                error=str(e),
                retry_seconds=_HOT_RELOAD_RETRY_DELAY_SECONDS,
            )
            time.sleep(_HOT_RELOAD_RETRY_DELAY_SECONDS)


@dataclass
class RequestContext:
    """Request-scoped context for handler processing.

    Groups request-scoped data that is passed to multiple helper methods
    to reduce parameter noise and improve code readability.

    Attributes:
        request_id: JSON-RPC request ID (str, int, or None)
        start_time: Request start time for duration calculation
        metrics: Metrics collector for observability
        rpc_request: Validated JSON-RPC request object
    """

    request_id: str | int | None
    start_time: float
    metrics: MetricsCollector
    rpc_request: JsonRpcRequest


@dataclass
class PreparedRequest:
    """Result of the shared request-preparation pipeline.

    Produced by :meth:`ASAPRequestHandler._prepare_request`, which runs the
    parse → auth → envelope → trace → sender → timestamp → nonce gate shared
    by ``handle_message``, ``_prepare_streaming_request`` and the WebSocket
    dispatch path (``asap.transport.ws._dispatch``). The caller owns detaching
    ``trace_token`` once it is done with the request (the pipeline detaches it
    on its own error returns).

    Attributes:
        ctx: Request-scoped context (request id, metrics, rpc_request)
        envelope: Validated ASAP envelope extracted from rpc_request params
        authenticated_agent_id: Agent id from auth middleware, or None
        trace_token: OpenTelemetry token to detach when processing ends, or None
    """

    ctx: RequestContext
    envelope: Envelope
    authenticated_agent_id: str | None
    trace_token: Any


# Deferred import: _request_handler needs RequestContext/PreparedRequest/RegistryHolder
# defined above. It also reads ``server.logger`` and ``server.is_debug_log_mode``
# through ``_server`` so tests can patch those module attributes. Importing it here
# breaks the cycle without copying bindings. Re-exported explicitly (``as`` alias)
# so mypy ``no_implicit_reexport`` exposes it and ``routes`` can type-hint against it.
from asap.transport._request_handler import (  # noqa: E402
    ASAPRequestHandler as ASAPRequestHandler,
)


@dataclass
class ServerComponents:
    """Resolved server components produced by :func:`_build_server_components`.

    Bundles the registry holder, request handler, identity stores, and
    env-defaulted config so :func:`create_app` stays a thin wiring function.

    Attributes:
        registry_holder: Hot-reload-aware handler registry holder
        handler: ASAP request handler bound to the registry/manifest/auth
        executor: Bounded thread pool (or None for unbounded)
        auth_middleware: Authentication middleware (or None when no auth)
        max_request_size: Resolved max request size in bytes
        nonce_store: Nonce replay store (or None when nonce validation off)
        identity_host_store: Resolved host identity store
        identity_agent_store: Resolved agent identity store
        identity_jti_cache: Host JWT jti replay cache
        identity_jwt_audience: Expected Host JWT ``aud`` value(s)
        identity_approval_store: Registration approval store
        snapshot_store: Resolved snapshot store
    """

    registry_holder: RegistryHolder
    handler: ASAPRequestHandler
    executor: BoundedExecutor | None
    auth_middleware: AuthenticationMiddleware | None
    max_request_size: int
    nonce_store: NonceStore | None
    identity_host_store: HostStore
    identity_agent_store: AgentStore
    identity_jti_cache: JtiReplayCacheProtocol
    identity_jwt_audience: str | list[str]
    identity_approval_store: ApprovalStore
    snapshot_store: SnapshotStore


def _build_server_components(
    *,
    manifest: Manifest,
    registry: HandlerRegistry | None,
    token_validator: Callable[[str], str | None] | None,
    max_request_size: int | None,
    max_threads: int | None,
    require_nonce: bool,
    hot_reload: bool | None,
    snapshot_store: SnapshotStore | None,
    metering_store: MeteringStore | None,
    metering_storage: object | None,
    identity_host_store: HostStore | None,
    identity_agent_store: AgentStore | None,
    identity_jti_cache: JtiReplayCacheProtocol | None,
    identity_jwt_audience: str | list[str] | None,
    identity_approval_store: ApprovalStore | None,
) -> ServerComponents:
    """Resolve the registry, handler, auth, identity stores, and config defaults.

    Pure setup (no FastAPI app) — extracted from :func:`create_app` so the
    wiring function stays thin.
    """
    executor: BoundedExecutor | None = None
    if max_threads is None:
        max_threads_env = os.getenv("ASAP_MAX_THREADS")
        if max_threads_env:
            max_threads = int(max_threads_env)
    if max_threads is not None:
        executor = BoundedExecutor(max_threads=max_threads)
        logger.info(
            "asap.server.bounded_executor_enabled",
            manifest_id=manifest.id,
            max_threads=max_threads,
        )

    # metering_storage takes precedence (enables usage API); the bridge adapts
    # it to the MeteringStore interface handlers expect.
    effective_metering_store: MeteringStore | None = metering_store
    if metering_storage is not None and isinstance(metering_storage, MeteringStorage):
        effective_metering_store = MeteringStorageBridge(metering_storage)

    use_default_registry = registry is None
    if registry is None:
        registry = create_default_registry(metering_store=effective_metering_store)
    elif effective_metering_store is not None:
        registry.set_metering_store(effective_metering_store)

    if executor is not None:
        registry._executor = executor

    registry_holder = RegistryHolder(registry)
    if executor is not None:
        registry_holder._executor = executor

    if hot_reload is None:
        hot_reload = os.getenv(ENV_HOT_RELOAD, "").strip().lower() in ("true", "1", "yes")

    auth_middleware: AuthenticationMiddleware | None = None
    if manifest.auth is not None:
        if token_validator is None:
            raise ValueError(
                "token_validator is required when manifest.auth is configured. "
                "Provide a function that validates tokens and returns agent IDs."
            )
        validator = BearerTokenValidator(token_validator)
        auth_middleware = AuthenticationMiddleware(manifest, validator)
        logger.info(
            "asap.server.auth_enabled",
            manifest_id=manifest.id,
            schemes=manifest.auth.schemes,
        )

    if max_request_size is None:
        max_request_size = int(os.getenv("ASAP_MAX_REQUEST_SIZE", str(MAX_REQUEST_SIZE)))

    nonce_store: NonceStore | None = None
    if require_nonce:
        nonce_store = InMemoryNonceStore()
        logger.info(
            "asap.server.nonce_validation_enabled",
            manifest_id=manifest.id,
        )

    resolved_host_store, resolved_agent_store = _resolve_identity_stores(
        identity_host_store, identity_agent_store
    )
    resolved_jti_cache = identity_jti_cache if identity_jti_cache is not None else JtiReplayCache()
    resolved_jwt_audience: str | list[str] = (
        identity_jwt_audience if identity_jwt_audience is not None else manifest.id
    )
    resolved_approval_store: ApprovalStore = (
        identity_approval_store if identity_approval_store is not None else InMemoryApprovalStore()
    )

    if snapshot_store is None:
        snapshot_store = create_snapshot_store()
        logger.info(
            "asap.server.snapshot_store_from_env",
            manifest_id=manifest.id,
            backend=os.environ.get("ASAP_STORAGE_BACKEND", "memory"),
        )

    handler = ASAPRequestHandler(
        registry_holder, manifest, auth_middleware, max_request_size, nonce_store
    )

    if hot_reload and use_default_registry:
        _start_handler_watcher(registry_holder, manifest_id=manifest.id)

    return ServerComponents(
        registry_holder=registry_holder,
        handler=handler,
        executor=executor,
        auth_middleware=auth_middleware,
        max_request_size=max_request_size,
        nonce_store=nonce_store,
        identity_host_store=resolved_host_store,
        identity_agent_store=resolved_agent_store,
        identity_jti_cache=resolved_jti_cache,
        identity_jwt_audience=resolved_jwt_audience,
        identity_approval_store=resolved_approval_store,
        snapshot_store=snapshot_store,
    )


def _resolve_identity_stores(
    host_store: HostStore | None,
    agent_store: AgentStore | None,
) -> tuple[HostStore, AgentStore]:
    """Resolve identity stores, defaulting both to in-memory when both omitted."""
    if host_store is None and agent_store is None:
        agent = InMemoryAgentStore()
        return InMemoryHostStore(agent_store=agent), agent
    if host_store is not None and agent_store is not None:
        return host_store, agent_store
    msg = "identity_host_store and identity_agent_store must both be set or both omitted"
    raise ValueError(msg)


def _start_handler_watcher(registry_holder: RegistryHolder, *, manifest_id: str) -> None:
    """Start the background handler file watcher for hot reload (default registry only)."""
    handlers_module = sys.modules.get("asap.transport.handlers")
    handlers_file = getattr(handlers_module, "__file__", "") if handlers_module else ""
    if not (handlers_file and Path(handlers_file).exists()):
        logger.warning(
            "asap.server.hot_reload_skipped",
            reason="handlers module path not found",
        )
        return
    watcher = threading.Thread(
        target=_run_handler_watcher,
        args=(registry_holder, handlers_file),
        name="asap-handler-watcher",
        daemon=True,
    )
    watcher.start()
    logger.info(
        "asap.server.hot_reload_enabled",
        manifest_id=manifest_id,
        path=handlers_file,
    )


def _create_fastapi_app(manifest: Manifest, components: ServerComponents) -> FastAPI:
    """Build the bare FastAPI app with docs gating and a graceful-shutdown lifespan."""
    _docs_url = "/docs" if is_debug_mode() else None
    _redoc_url = "/redoc" if is_debug_mode() else None
    _openapi_url = "/openapi.json" if is_debug_mode() else None

    active_websockets: set[WebSocket] = set()
    sla_breach_subscribers: set[WebSocket] = set()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> Any:
        yield
        for ws in list(active_websockets):
            with suppress(OSError):
                await ws.close(
                    code=WS_CLOSE_GOING_AWAY,
                    reason=WS_CLOSE_REASON_SHUTDOWN,
                )

    app = FastAPI(
        title="ASAP Protocol Server",
        description=f"ASAP server for {manifest.name}",
        version=manifest.version,
        docs_url=_docs_url,
        redoc_url=_redoc_url,
        openapi_url=_openapi_url,
        lifespan=lifespan,
    )
    # Stash the connection sets so the lifespan closure can reach them via app.state.
    app.state.websocket_connections = active_websockets
    app.state.sla_breach_subscribers = sla_breach_subscribers
    # Core handler + manifest + start timestamp for the route-group routers.
    app.state.request_handler = components.handler
    app.state.manifest = manifest
    app.state.server_started_at = time.monotonic()
    return app


def _populate_app_state(
    app: FastAPI,
    *,
    components: ServerComponents,
    manifest: Manifest,
    max_request_size: int,
    max_batch_size: int,
    snapshot_store: SnapshotStore,
    metering_store: MeteringStore | None,
    audit_store: AuditStore | None,
    websocket_message_rate_limit: float | None,
    mtls_config: MTLSConfig | None,
    identity_host_supports_ciba: bool,
    identity_approval_a2h_channel: A2HApprovalChannel | None,
    identity_fresh_session_config: FreshSessionConfig | None,
    identity_webauthn_verifier: WebAuthnVerifier | None,
    identity_rate_limit: str | None,
) -> None:
    """Populate ``app.state`` with identity, snapshot, and feature config."""
    app.state.websocket_message_rate_limit = websocket_message_rate_limit
    app.state.mtls_config = mtls_config
    app.state.identity_host_store = components.identity_host_store
    app.state.identity_agent_store = components.identity_agent_store
    app.state.identity_jti_cache = components.identity_jti_cache
    app.state.identity_jwt_audience = components.identity_jwt_audience
    app.state.identity_approval_store = components.identity_approval_store
    app.state.identity_host_supports_ciba = identity_host_supports_ciba
    if identity_approval_a2h_channel is not None:
        app.state.identity_approval_a2h_channel = identity_approval_a2h_channel
    if identity_fresh_session_config is not None:
        app.state.identity_fresh_session_config = identity_fresh_session_config
    app.state.identity_webauthn_verifier = (
        identity_webauthn_verifier
        if identity_webauthn_verifier is not None
        else default_webauthn_verifier()
    )
    identity_rl = identity_rate_limit or "5/second;30/minute"
    app.state.identity_limiter = create_limiter([identity_rl])
    app.state.max_request_size = max_request_size
    app.state.max_batch_size = max_batch_size
    app.state.snapshot_store = snapshot_store
    app.state.metering_store = metering_store
    app.state.audit_store = audit_store
    # Unauthenticated-operator-API warnings are emitted after the
    # ``require_operator_auth`` flag is known (#209).
    _ = manifest  # manifest already stored on app.state by _create_fastapi_app


def _warn_if_operator_api_unauthenticated(
    event: str, display: str, path: str, *, require_auth: bool
) -> None:
    """Warn that an operator API is exposed without auth (skipped when protected).

    When ``require_auth`` is True the API is guarded by OAuth2 scope ``asap:admin``
    (aggregate ``asap.server.operator_api_auth_enabled`` log), so no warning fires.
    """
    if require_auth:
        return
    logger.warning(
        event,
        message=(
            f"{display} ({path}) is enabled but unauthenticated. "
            "Intended for local/operator use only. Protect with OAuth2 or network "
            "controls when exposed (create_app(require_operator_auth=True))."
        ),
    )


def _wire_optional_routers(
    app: FastAPI,
    *,
    registry_auto_registration: "AutoRegistrationConfig | None",
    metering_storage: object | None,
    sla_storage: object | None,
    oauth2_config: OAuth2Config | None,
    delegation_key_store: Callable[[str], Any] | None,
    delegation_storage: object | None,
    mtls_config: MTLSConfig | None,
    manifest: Manifest,
    require_operator_auth: bool = False,
) -> None:
    """Include feature-flagged routers: identity, capability, registry, usage, SLA, delegation."""
    app.include_router(create_agent_identity_router())
    app.include_router(create_escalation_router())

    # Capability-based authorization (S1)
    from asap.auth.capabilities import CapabilityRegistry

    if not hasattr(app.state, "capability_registry"):
        app.state.capability_registry = CapabilityRegistry()
    app.include_router(create_capability_router())

    if registry_auto_registration is not None:
        from asap.registry.auto_registration import create_auto_registration_router
        from asap.registry.receipt_cache import create_registration_receipt_cache

        app.state.registration_limiter = create_registration_rate_limiter()
        app.state.registration_receipt_cache = create_registration_receipt_cache()
        app.include_router(create_auto_registration_router(registry_auto_registration))
        logger.info(
            "asap.server.registry_auto_registration_enabled",
            manifest_id=manifest.id,
        )

    if metering_storage is not None and isinstance(metering_storage, MeteringStorage):
        app.state.metering_storage = metering_storage
        app.include_router(create_usage_router(require_auth=require_operator_auth))
        _warn_if_operator_api_unauthenticated(
            "asap.server.usage_api_unauthenticated",
            "Usage API",
            "/usage",
            require_auth=require_operator_auth,
        )

    if mtls_config is not None:
        logger.info(
            "asap.server.mtls_enabled",
            manifest_id=manifest.id,
            cert_file=str(mtls_config.cert_file),
        )

    if sla_storage is not None and isinstance(sla_storage, SLAStorage):
        app.state.sla_storage = sla_storage
        app.include_router(create_sla_router(require_auth=require_operator_auth))
        _warn_if_operator_api_unauthenticated(
            "asap.server.sla_api_unauthenticated",
            "SLA API",
            "/sla",
            require_auth=require_operator_auth,
        )

    if oauth2_config is not None and delegation_key_store is not None:
        app.state.delegation_key_store = delegation_key_store
        if delegation_storage is not None:
            app.state.delegation_storage = delegation_storage
        app.include_router(create_delegation_router(), prefix="/asap")
        logger.info(
            "asap.server.delegation_api_enabled",
            manifest_id=manifest.id,
        )


def _wire_middleware(
    app: FastAPI,
    *,
    oauth2_config: OAuth2Config | None,
    max_request_size: int,
    asap_challenge_enabled: bool,
    asap_challenge_discovery_url: str | None,
    asap_challenge_path_prefixes: tuple[str, ...] | None,
    manifest: Manifest,
    require_operator_auth: bool = False,
) -> None:
    """Add the size-limit, OAuth2, ASAP-version, and WWW-Authenticate middlewares."""
    # Size limit runs before routing.
    app.add_middleware(SizeLimitMiddleware, max_size=max_request_size)

    if oauth2_config is not None:
        middleware_kwargs: dict[str, Any] = {
            "jwks_uri": oauth2_config.jwks_uri,
            "required_scope": oauth2_config.required_scope,
            "path_prefix": oauth2_config.path_prefix,
            "manifest_id": manifest.id,
            "custom_claim": oauth2_config.custom_claim,
            "expected_issuer": oauth2_config.expected_issuer,
            "expected_audience": oauth2_config.expected_audience,
        }
        if require_operator_auth:
            # Keep OAuth2Config.required_scope untouched so /asap tasks are not
            # forced to asap:admin; admin is enforced on operator routers via Depends.
            middleware_kwargs["extra_path_prefixes"] = OPERATOR_API_PATH_PREFIXES
        if oauth2_config.jwks_fetcher is not None:
            middleware_kwargs["jwks_fetcher"] = oauth2_config.jwks_fetcher
        # Execute always requires and verifies its own bearer (Agent JWT, or an external
        # token when configured), like /asap/agent/*. Behind the middleware it could never
        # succeed: an Agent JWT failed the IdP JWKS check.
        middleware_kwargs["exempt_paths"] = frozenset({CAPABILITY_EXECUTE_PATH})
        # ONE canonical JWKSValidator shared by both transports so the HTTP
        # middleware stack instance and the WS ``app.state`` instance do not
        # keep divergent JWKS caches (S0 #237). Both middleware instances are
        # constructed with the same ``validator``; the WS path reads the
        # ``app.state`` instance and calls ``validate_bearer_token`` directly,
        # which delegates to this shared validator.
        shared_validator = JWKSValidator(
            oauth2_config.jwks_uri,
            fetcher=oauth2_config.jwks_fetcher,
            expected_issuer=oauth2_config.expected_issuer,
            expected_audience=oauth2_config.expected_audience,
            require_exp=False,
        )
        middleware_kwargs["validator"] = shared_validator
        oauth2_middleware = OAuth2Middleware(app, **middleware_kwargs)
        app.add_middleware(OAuth2Middleware, **middleware_kwargs)
        # Expose the middleware instance so the WS path can enforce OAuth2 at
        # connection acceptance; the HTTP middleware stack never runs over WS
        # (B4/BUG #4). Stored on app.state to keep server.py decoupled from the
        # WS module's signature.
        app.state.oauth2_middleware = oauth2_middleware
        logger.info(
            "asap.server.oauth2_enabled",
            manifest_id=manifest.id,
            jwks_uri=oauth2_config.jwks_uri,
            path_prefix=oauth2_config.path_prefix,
            operator_auth=require_operator_auth,
        )

    app.add_middleware(ASAPVersionMiddleware)

    if asap_challenge_enabled:
        from asap.transport.challenge import (
            WWWAuthenticateASAPMiddleware,
            default_manifest_discovery_url,
        )

        disc = asap_challenge_discovery_url or default_manifest_discovery_url(
            manifest.endpoints.asap
        )
        app.add_middleware(
            WWWAuthenticateASAPMiddleware,
            default_discovery_url=disc,
            path_prefixes=asap_challenge_path_prefixes,
        )


def _wire_core_routes(
    app: FastAPI,
    *,
    manifest: Manifest,
    rate_limit: str | None,
    require_operator_auth: bool = False,
) -> None:
    """Include the core route groups: health/discovery/metrics, JSON-RPC, WS, audit."""
    _ = manifest  # routers read manifest from app.state
    _ = rate_limit  # rate limiting wired separately via _configure_rate_limiting
    app.include_router(create_health_router())
    app.include_router(create_jsonrpc_router())
    app.include_router(create_websocket_router())
    app.include_router(create_audit_router(require_auth=require_operator_auth))


def _configure_rate_limiting(
    app: FastAPI,
    *,
    rate_limit: str | None,
    manifest: Manifest,
) -> None:
    """Create the per-app rate limiter and register the 429 exception handler."""
    if rate_limit is None:
        # Default matches DD-012: burst allowance for bursty agent traffic.
        rate_limit_str = os.getenv("ASAP_RATE_LIMIT", "10/second;100/minute")
    else:
        rate_limit_str = rate_limit

    # Isolated per-app limiter storage; tests override via app.state.limiter.
    app.state.limiter = create_limiter(
        [rate_limit_str],
        key_func=_get_sender_from_envelope,
    )
    app.add_exception_handler(RateLimitExceeded, rate_limit_handler)
    logger.info(
        "asap.server.rate_limit_enabled",
        manifest_id=manifest.id,
        rate_limit=rate_limit_str,
    )
    logger.info(
        "asap.server.max_request_size",
        manifest_id=manifest.id,
        max_request_size=app.state.max_request_size,
    )


def create_app(
    manifest: Manifest,
    registry: HandlerRegistry | None = None,
    token_validator: Callable[[str], str | None] | None = None,
    oauth2_config: OAuth2Config | None = None,
    rate_limit: str | None = None,
    max_request_size: int | None = None,
    max_threads: int | None = None,
    require_nonce: bool = False,
    hot_reload: bool | None = None,
    snapshot_store: SnapshotStore | None = None,
    metering_store: MeteringStore | None = None,
    metering_storage: object | None = None,
    websocket_message_rate_limit: float | None = 10.0,
    mtls_config: MTLSConfig | None = None,
    delegation_key_store: Callable[[str], Any] | None = None,
    delegation_storage: object | None = None,
    sla_storage: object | None = None,
    audit_store: AuditStore | None = None,
    identity_host_store: HostStore | None = None,
    identity_agent_store: AgentStore | None = None,
    identity_jti_cache: JtiReplayCacheProtocol | None = None,
    identity_jwt_audience: str | list[str] | None = None,
    identity_rate_limit: str | None = None,
    identity_approval_store: ApprovalStore | None = None,
    identity_host_supports_ciba: bool = True,
    identity_approval_a2h_channel: A2HApprovalChannel | None = None,
    identity_fresh_session_config: FreshSessionConfig | None = None,
    identity_webauthn_verifier: WebAuthnVerifier | None = None,
    max_batch_size: int = DEFAULT_MAX_BATCH_SIZE,
    registry_auto_registration: AutoRegistrationConfig | None = None,
    asap_challenge_enabled: bool = True,
    asap_challenge_discovery_url: str | None = None,
    asap_challenge_path_prefixes: tuple[str, ...] | None = (
        "/asap/capability",
        "/asap/agent",
    ),
    require_operator_auth: bool = False,
    external_token_config: ExternalTokenConfig | None = None,
    agent_card: AgentCard | None = None,
) -> FastAPI:
    """Create and configure a FastAPI application for ASAP protocol.

    This factory function creates a FastAPI app with:
    - POST /asap endpoint for handling ASAP messages via JSON-RPC
    - WebSocket /asap/ws for real-time JSON-RPC (same protocol as POST /asap)
    - GET /.well-known/asap/manifest.json for agent discovery
    - GET /.well-known/agent-card.json, when ``agent_card`` is set (A2A v1.0 discovery)
    - GET /asap/metrics for Prometheus-compatible metrics
    - Authentication middleware (if manifest.auth is configured)
    - Error handling middleware
    - Request validation
    - Extensible handler registry for payload processing

    Args:
        manifest: The agent's manifest describing capabilities and endpoints
        registry: Optional handler registry for processing payloads.
            If None, a default registry with echo handler is created.
        token_validator: Optional function to validate Bearer tokens.
            Required if manifest.auth is configured. Should return agent ID
            if token is valid, None otherwise.
        oauth2_config: When set, OAuth2Middleware validates IdP JWTs for ``path_prefix``
            routes; ``/asap/agent/*`` uses Host JWT instead, and ``POST
            /asap/capability/execute`` verifies its own Agent JWT (or external token).
            Without it, ``/asap`` is open unless ``manifest.auth`` and ``token_validator``
            are configured.
        rate_limit: Optional rate limit string (e.g., "10/second;100/minute").
            Rate limiting is IP-based (per client IP address) to prevent DoS attacks.
            Uses token bucket pattern: burst limit + sustained limit.
            Defaults to ASAP_RATE_LIMIT environment variable or "10/second;100/minute".
            **Warning:** The default storage is ``memory://`` (per-process). In
            multi-worker deployments (e.g., Gunicorn with 4 workers), each worker
            has isolated limits, so effective rate = limit × workers (e.g.,
            10/s → 40/s). For production, use Redis-backed storage.
        max_request_size: Optional maximum request size in bytes.
            Defaults to ASAP_MAX_REQUEST_SIZE environment variable or 10MB.
        max_threads: Optional maximum number of threads for sync handlers.
            Defaults to ASAP_MAX_THREADS environment variable or min(32, cpu_count + 4).
            Set to None to use unbounded executor (not recommended for production).
        require_nonce: If True, enables nonce validation for replay attack prevention.
            When enabled, creates an InMemoryNonceStore and validates nonces in envelopes.
            Defaults to False (nonce validation is optional).
        hot_reload: If True, watch handlers.py and reload handler registry on file change
            (development only). Defaults to ASAP_HOT_RELOAD env or False.
        snapshot_store: Optional SnapshotStore for state persistence. If None, uses
            create_snapshot_store() (ASAP_STORAGE_BACKEND and ASAP_STORAGE_PATH).
            Stored on app.state.snapshot_store for handlers.
        metering_store: Optional MeteringStore for usage recording. When set, task.request
            completions are recorded (tokens, duration, api_calls from TaskResponse.metrics).
        websocket_message_rate_limit: Max messages per second per WebSocket connection.
            When set (default 10.0), connections exceeding this are sent an error frame
            and closed. Set to None to disable WebSocket message rate limiting.
        mtls_config: Optional mTLS config for server. When provided, store on app.state.mtls_config.
            Use asap.transport.mtls.mtls_config_to_uvicorn_kwargs() when running uvicorn.
        delegation_key_store: Optional callable (delegator_urn: str) -> Ed25519PrivateKey for
            signing delegation tokens. When provided together with oauth2_config, enables
            POST /asap/delegations (creates JWT delegation token; authenticated agent is issuer).
        delegation_storage: Optional DelegationStorage for revocation. When provided with
            delegation_key_store, enables DELETE /asap/delegations/{id} and registers issued
            token IDs so only the delegator can revoke.
        sla_storage: Optional SLAStorage for SLA metrics and breaches. When provided,
            enables GET /sla, /sla/history, /sla/breaches for querying SLA status.
        audit_store: Optional AuditStore for tamper-evident audit logging. When provided,
            enables GET /audit for querying the log and automatically records successful
            message processing events.
        identity_host_store: Optional HostStore; default in-memory when both stores omitted.
        identity_agent_store: Optional AgentStore; must be set with identity_host_store or both omitted.
        identity_jti_cache: Optional ``jti`` replay cache for Host/Agent JWT routes.
            Mutating routes record ``jti`` values; ``GET /asap/agent/status`` uses
            the same cache in read-only mode so polling can reuse a token while
            still rejecting Host JWTs already consumed elsewhere. Defaults to a
            new in-memory :class:`~asap.auth.agent_jwt.JtiReplayCache` per app.
            For multi-worker deployments, pass
            :class:`~asap.auth.jti_replay_cache.RedisJtiReplayCache` (requires
            the ``redis`` extra).
        identity_jwt_audience: Expected ``aud`` value(s) for Host JWT verification on
            ``/asap/agent/*``. Defaults to ``manifest.id`` so tokens are bound to this
            server instance. Set explicitly for multi-audience or migration scenarios.
        identity_rate_limit: Rate limit string for ``/asap/agent/*`` endpoints.
            Defaults to ``"5/second;30/minute"`` (tighter than the main limiter).
            Uses a separate budget from the main ``rate_limit``.
        identity_approval_store: Store for registration approval flows (Device Auth / CIBA).
            Defaults to an in-memory store per process.
        identity_host_supports_ciba: When True and the host has ``user_id``, CIBA may be
            selected over Device Authorization.
        identity_approval_a2h_channel: Optional channel that resolves pending approvals
            via A2H in a background task after register.
        identity_fresh_session_config: When set, registration requiring approval and
            pending approval polling require a Host JWT issued within ``window_seconds``.
        identity_webauthn_verifier: Optional async verifier for ``webauthn`` assertions
            when ``require_webauthn_for`` lists requested capabilities. When omitted,
            :func:`asap.auth.self_auth.default_webauthn_verifier` is used (real verification
            only if the ``webauthn`` extra is installed and ``ASAP_WEBAUTHN_RP_ID`` plus
            ``ASAP_WEBAUTHN_ORIGIN`` are set; otherwise the placeholder verifier applies).
        registry_auto_registration: When set, mounts ``POST /registry/agents`` for Lite Registry
            self-service registration. Requires OAuth2 JWT validation on this path: set
            ``oauth2_config.path_prefix`` to ``"/"`` (or a prefix covering ``/registry``) so
            :class:`~asap.auth.middleware.OAuth2Middleware` applies; rate limits use
            ``app.state.registration_limiter`` (5/hour per Bearer token).
        require_operator_auth: When True, require a valid OAuth2 Bearer JWT with scope
            ``asap:admin`` on ``/usage``, ``/sla``, and ``/audit``. Requires
            ``oauth2_config``. Default False preserves local/operator open access
            (startup warnings remain when those APIs are enabled without auth).
        external_token_config: Allowlist of external OAuth issuers (COMPAT-001). When set,
            ``POST /asap/capability/execute`` also accepts their access tokens instead of an
            Agent JWT; each principal gets only the grants of its issuer's rules. Default None
            keeps that route on Agent JWT only. To accept the ``oauth2_config`` IdP's tokens
            on that route, list it here. ``OAuth2Middleware`` skips that route, so for those
            tokens these ``oauth2_config`` checks stop applying there: ``required_scope``
            (the entry's grant rules decide instead), the identity binding (the custom claim
            or the ``ASAP_AUTH_SUBJECT_MAP`` allowlist), and ``expected_issuer`` and
            ``expected_audience`` (the entry's ``issuer`` and ``audience`` are checked
            instead). Give that entry ``required_claims={custom_claim: manifest.id}``, where
            ``custom_claim`` is the middleware's claim (``oauth2_config.custom_claim``, else
            ``ASAP_AUTH_CUSTOM_CLAIM``, else ``DEFAULT_CUSTOM_CLAIM``), to restore the
            custom-claim binding; the subject allowlist has no equivalent. Without it, a
            token the IdP minted for another agent with the same audience executes here,
            and ``create_app`` logs ``asap.server.external_issuer_unbound``.
        agent_card: A signed A2A v1.0 Agent Card for this agent, from
            :func:`asap.adapters.a2a.build_signed_agent_card` (COMPAT-003). When set,
            ``GET /.well-known/agent-card.json`` serves it beside the manifest, with the
            same caching headers. It must carry a signature and name this agent: the same
            name and version, and the URN of its ASAP extension equal to ``manifest.id``.
            Default None: the route does not exist.

    Returns:
        Configured FastAPI application ready to run

    Raises:
        ValueError: If manifest requires authentication but no token_validator provided,
            if ``require_operator_auth`` is True without ``oauth2_config``, or if
            ``agent_card`` is unsigned or describes another agent.

    Example:
        >>> from asap.models.entities import Manifest, Capability, Endpoint, Skill, AuthScheme, SLADefinition
        >>> from asap.transport.handlers import HandlerRegistry
        >>> manifest = Manifest(
        ...     id="urn:asap:agent:test",
        ...     name="Test Agent",
        ...     version="1.0.0",
        ...     description="Test agent",
        ...     capabilities=Capability(
        ...         asap_version="0.1",
        ...         skills=[Skill(id="test", description="Test skill")],
        ...         state_persistence=False
        ...     ),
        ...     endpoints=Endpoint(asap="http://localhost:8000/asap")
        ... )
        >>> app = create_app(manifest)
        >>>
        >>> # With SLA (optional): add sla=SLADefinition(availability="99.5%", max_latency_p95_ms=500)
        >>>
        >>> # With authentication:
        >>> manifest_with_auth = Manifest(
        ...     ...,  # same as above
        ...     auth=AuthScheme(schemes=["bearer"])
        ... )
        >>> def my_token_validator(token: str) -> str | None:
        ...     if token == "valid-token":
        ...         return "urn:asap:agent:client"
        ...     return None
        >>> app = create_app(manifest_with_auth, token_validator=my_token_validator)
        >>>
        >>> # With custom registry:
        >>> registry = HandlerRegistry()
        >>> registry.register("task.request", my_handler)
        >>> app = create_app(manifest, registry)
        >>> # Run with uvicorn: uvicorn module:app
    """
    if require_operator_auth and oauth2_config is None:
        raise ValueError(
            "require_operator_auth=True requires oauth2_config so /usage, /sla, and "
            "/audit can validate Bearer JWTs with scope asap:admin"
        )
    if agent_card is not None:
        validate_served_agent_card(agent_card, manifest)

    components = _build_server_components(
        manifest=manifest,
        registry=registry,
        token_validator=token_validator,
        max_request_size=max_request_size,
        max_threads=max_threads,
        require_nonce=require_nonce,
        hot_reload=hot_reload,
        snapshot_store=snapshot_store,
        metering_store=metering_store,
        metering_storage=metering_storage,
        identity_host_store=identity_host_store,
        identity_agent_store=identity_agent_store,
        identity_jti_cache=identity_jti_cache,
        identity_jwt_audience=identity_jwt_audience,
        identity_approval_store=identity_approval_store,
    )
    # Re-bind resolved values so the wiring below uses env-defaulted config.
    max_request_size = components.max_request_size
    snapshot_store = components.snapshot_store

    app = _create_fastapi_app(manifest, components)
    _populate_app_state(
        app,
        components=components,
        manifest=manifest,
        max_request_size=max_request_size,
        max_batch_size=max_batch_size,
        snapshot_store=snapshot_store,
        metering_store=metering_store,
        audit_store=audit_store,
        websocket_message_rate_limit=websocket_message_rate_limit,
        mtls_config=mtls_config,
        identity_host_supports_ciba=identity_host_supports_ciba,
        identity_approval_a2h_channel=identity_approval_a2h_channel,
        identity_fresh_session_config=identity_fresh_session_config,
        identity_webauthn_verifier=identity_webauthn_verifier,
        identity_rate_limit=identity_rate_limit,
    )
    if external_token_config is not None:
        app.state.external_token_verifier = ExternalTokenVerifier(external_token_config)
        warn_if_oauth2_idp_unbound(external_token_config, oauth2_config, manifest_id=manifest.id)
    if audit_store is not None:
        _warn_if_operator_api_unauthenticated(
            "asap.server.audit_api_unauthenticated",
            "Audit API",
            "/audit",
            require_auth=require_operator_auth,
        )
    if require_operator_auth:
        logger.info(
            "asap.server.operator_api_auth_enabled",
            paths=list(OPERATOR_API_PATH_PREFIXES),
            required_scope="asap:admin",
        )
    _wire_optional_routers(
        app,
        registry_auto_registration=registry_auto_registration,
        metering_storage=metering_storage,
        sla_storage=sla_storage,
        oauth2_config=oauth2_config,
        delegation_key_store=delegation_key_store,
        delegation_storage=delegation_storage,
        mtls_config=mtls_config,
        manifest=manifest,
        require_operator_auth=require_operator_auth,
    )
    _wire_middleware(
        app,
        oauth2_config=oauth2_config,
        max_request_size=max_request_size,
        asap_challenge_enabled=asap_challenge_enabled,
        asap_challenge_discovery_url=asap_challenge_discovery_url,
        asap_challenge_path_prefixes=asap_challenge_path_prefixes,
        manifest=manifest,
        require_operator_auth=require_operator_auth,
    )
    _wire_core_routes(
        app,
        manifest=manifest,
        rate_limit=rate_limit,
        require_operator_auth=require_operator_auth,
    )
    if agent_card is not None:
        app.state.agent_card = agent_card
        app.include_router(create_agent_card_router())
    _configure_rate_limiting(app, rate_limit=rate_limit, manifest=manifest)
    # OpenTelemetry tracing (zero-config via OTEL_* env vars)
    configure_tracing(service_name=manifest.id, app=app)
    return app


def _create_default_manifest() -> Manifest:
    """Create a default manifest for standalone server execution.

    This manifest is used when running the server directly via uvicorn
    without providing a custom manifest.

    Returns:
        Default manifest with basic echo capabilities
    """
    return Manifest(
        id="urn:asap:agent:default-server",
        name="ASAP Default Server",
        version="1.0.0-dev",
        description="Default ASAP protocol server with echo capabilities",
        capabilities=Capability(
            asap_version="0.1",
            skills=[Skill(id="echo", description="Echo back the input")],
            state_persistence=False,
        ),
        endpoints=Endpoint(asap="http://localhost:8000/asap"),
    )


# Default app instance for direct uvicorn execution:
#   uvicorn asap.transport.server:app --host 0.0.0.0 --port 8000
app = create_app(_create_default_manifest(), create_default_registry())
