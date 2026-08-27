# Changelog

All notable changes to the ASAP Protocol will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security

- **Agent revocation permanence** — Concurrent ``POST /asap/agent/rotate-key``,
  ``POST /asap/agent/reactivate``, and ``GET /asap/agent/status``
  (pending→active or pending→rejected) can no longer resurrect a revoked agent
  via a stale get→mutate→full-row ``save``. ``AgentStore.save`` implementations
  MUST atomically refuse replacing a ``revoked`` row with a non-revoked snapshot
  and raise ``RevokedAgentOverwriteError``. ``InMemoryAgentStore.save`` enforces
  this (check and dict write with no await). ``save_agent_unless_revoked`` is a
  named wrapper around ``save`` and does not re-get.

### Security (deps)

- Raise `cryptography` to `>=50.0.0,<51` for **PYSEC-2026-3552** (PKCS7
  Bleichenbacher; 49.0.0 already covered PYSEC-2026-3553/3554). Pair with
  `pyopenssl>=26.4.0` so `[webauthn]` stays importable.
- Raise `aiohttp` override to `>=3.14.3,<4` for **PYSEC-2026-3545/3546/3547**.
- Add `h2>=4.4.1` override for **PYSEC-2026-3628**.
- Raise `pip` floor to `>=26.2` for **PYSEC-2026-3721**.
- Raise optional `[pydanticai]` floor to `pydantic-ai>=1.106.0,<2` for
  **PYSEC-2026-3692/3693**.
- `apps/web` npm overrides: `fast-uri@^3.1.5`, `nanoid@^3.3.18`,
  `postcss@^8.5.23`, `brace-expansion@^5.0.9`, `ip-address@^10.3.1`,
  `js-yaml@^4.3.1`, and `undici@^7.29.0` so production moderate+ and
  full-graph high+ audits stay clean.

### Fixed

- **Agent JWT session sliding (LIFE-005)** — Successful
  :func:`~asap.auth.agent_jwt.verify_agent_jwt` always persists the extended
  ``last_used_at`` after a verified Agent JWT (restores the pre-S3 “verifier
  writes” contract; callers must not assume they alone persist idle timeout).
  Side effect: authenticated ``GET /asap/capability/list`` with an Agent JWT
  now slides idle timeout the same as other verify paths — list traffic keeps
  sessions warm.
- **Agent JWT verify vs concurrent revoke/rotate** — Session extension no
  longer uses full-row ``AgentStore.save`` of a verify-time snapshot (that
  TOCTOU could resurrect a revoked agent or undo ``rotate-key``). Persist goes
  through ``AgentStore.touch_if_current`` (atomic ``UPDATE ... WHERE status =
  'active'`` plus matching ``host_id`` and RFC 7638 public-key thumbprint).
  Custom store authors must implement that method; get→mutate→save is not
  sufficient.

### Follow-up (planned v2.5.5+)

- **Formal Spec & Interop** — RFC spec, introspection, privacy ([prd-v2.5.5-formal-spec-interop.md](product/prd/prd-v2.5.5-formal-spec-interop.md)).
- `@asap-protocol/mcp-auth` HTTP/SSE middleware (deferred from v2.5.0 — see [2.5.0] TypeScript note and [typescript-mcp-auth-spike.md](engineering/tasks/v2.5.0/typescript-mcp-auth-spike.md)).
- Collapse the dual `UsageMetrics`/`InMemoryMeteringStore` pair retained in S1 for API stability.
- Reduce the `asap.transport.client` package aggregate LOC below the S2 target.

---

## [2.5.4] - 2026-07-18

**Distribution Loop** — homepage, thin starters, Build for agents guide, and maintainer
telemetry ops (no public metrics UI). Scope:
[prd-v2.5.4-distribution-loop.md](product/prd/prd-v2.5.4-distribution-loop.md).
npm `@asap-protocol/*` packages remain at **2.4.1**. Tag [`v2.5.4`](https://github.com/adriannoes/asap-protocol/releases/tag/v2.5.4) · PyPI `asap-protocol==2.5.4`.

### Added

- **Thin starters** under `examples/starters/`:
  - `openapi-provider/` — OpenAPI → ASAP skills wrapper
  - `typescript-consumer/` — `@asap-protocol/client` consumer smoke
  - `mcp-auth-bridge/` — MCP Auth Bridge (`protect_server`) wrapper
- **Build for agents guide** — [docs/guides/build-for-agents.md](docs/guides/build-for-agents.md)
  (agent-first onboarding into the three starters).
- **Homepage agent-first narrative** — hero / CTAs route to docs, starters, and
  examples (marketplace browse/register remain secondary).
- **Telemetry ops** — collector defaults expanded (npm ≥3 scoped packages,
  PyPI ≥2); maintainer runbook; **no** public live metrics UI (PRD D4).

### Security (deps)

- Raise optional `[mcp]` floor to `mcp>=1.28.1` for **CVE-2026-52869**,
  **CVE-2026-52870**, and **CVE-2026-59950**. See [SECURITY.md](SECURITY.md).

### Migration

- **v2.5.3 → v2.5.4**: See [migration guide](docs/migration.md#upgrading-from-v253-to-v254).
  No breaking wire-protocol changes; adopt starters/guide as needed.

---

## [2.5.3] - 2026-07-14

**Adapter Lab II** — workflow / enterprise adapters as **docs + examples** (no wire-format
change). Scope: [prd-v2.5.3-adapter-lab-ii.md](product/prd/prd-v2.5.3-adapter-lab-ii.md).
npm `@asap-protocol/*` packages remain at **2.4.1**. Tag [`v2.5.3`](https://github.com/adriannoes/asap-protocol/releases/tag/v2.5.3) · PyPI `asap-protocol==2.5.3`.

### Added

- **Workflow connectors**: OpenAPI → ASAP skills for n8n-/Activepieces-shaped workflow
  HTTP APIs — [guide](docs/integrations/workflow-connectors.md) +
  `examples/workflow_asap_connector/` (Compliance Harness v2 score **1.0**).
- **Automation connector security**: production baseline for OpenAPI-backed connectors
  ([guide](docs/guides/automation-connector-security.md)).
- **Microsoft Agent Framework**: research / experimental interop guide
  ([guide](docs/integrations/microsoft-agent-framework.md)); no .NET SDK / NuGet sample.
- **NeMo Agent Toolkit**: experimental Path A stdio demo
  ([guide](docs/integrations/nemo-agent-toolkit.md),
  `examples/nemo_agent_toolkit_asap/`); Path C third-party plugin remains deferred.
- **Docs / web surface**: MkDocs Adapters + Lab II nav; marketplace WhatsNew ribbon and
  developer-experience CTAs for Lab II guides.

### Fixed

- **Invalid envelope JSON-RPC errors**: URN / structure validation failures return
  JSON-safe `-32602` with `validation_errors` (no longer escalate to `-32603` when
  Pydantic embeds `ValueError` in `ctx`).
- **MCP Auth Bridge example client**: self-contained path captures the demo Agent JWT
  from the spawned child stderr (cross-process paste no longer required).
- **`ASAPClient.health_check`**: `base_url` defaults to the client's HTTP base URL.

### Security (deps)

- Override `setuptools>=83.0.0` for **PYSEC-2026-3447** (transitive build/tooling path).
  See [SECURITY.md](SECURITY.md).

### Migration

- **v2.5.2 → v2.5.3**: See [migration guide](docs/migration.md#upgrading-from-v252-to-v253).
  No breaking wire-protocol changes; adopt new guides/examples as needed.

---

## [2.5.2] - 2026-07-08

**Security & correctness follow-up** — operator API auth, tighter ingress
validation, Redis JTI replay, web distributed rate limits, v2.5.1 CR follow-ups,
and registry fixes. Scope: [prd-v2.5.2-security-follow-up.md](product/prd/prd-v2.5.2-security-follow-up.md).
Wire protocol and manifest schema are unchanged aside from rejecting unknown
`config` / `metadata` keys (see Migration).

### Added

- **Opt-in operator API auth (#209)**: `create_app(require_operator_auth=True)`
  requires an OAuth2 Bearer JWT with scope ``asap:admin`` on ``/usage``,
  ``/sla``, and ``/audit``. Default remains open (local/operator ergonomics)
  with the existing startup warnings. Requires ``oauth2_config``.
- **Redis-backed JTI replay cache (#209)**: Optional
  :class:`~asap.auth.jti_replay_cache.RedisJtiReplayCache` for multi-worker
  Host/Agent JWT replay protection. Default remains in-memory
  :class:`~asap.auth.agent_jwt.JtiReplayCache`. Requires
  ``pip install 'asap-protocol[redis]'``. Inject via
  ``create_app(identity_jti_cache=...)`` or ``MCPAuthConfig.jti_replay_cache``.

### Changed

- **Ingress validation (#209)**: `TaskRequestConfig` and `CommonMetadata` now reject
  unknown fields (`extra="forbid"`, inherited from `ASAPBaseModel`). Previously these
  models allowed arbitrary extension keys; clients sending custom `config` or
  `metadata` properties must use known fields only or nest extensions under
  `TaskRequest.input` / envelope `extensions`. See
  [migration guide](docs/migration.md#upgrading-from-v251).
- **CLI legacy imports (#242)**: `DEFAULT_SCHEMAS_DIR`, `export_all_schemas`, and
  `_repl_namespace` are no longer re-exported from `asap.cli` root. Import from
  `asap.cli._compat` (shim) or the owning submodules (`asap.cli.schemas`,
  `asap.schemas`, `asap.cli.repl`). The `_compat` shim is removed in v2.6.0
  ([#275](https://github.com/adriannoes/asap-protocol/issues/275)).
  See [migration guide](docs/migration.md#upgrading-from-v251).
- **Web app (`apps/web`)**: Optional distributed rate limits via Upstash Redis
  or Vercel KV when `UPSTASH_REDIS_REST_URL` + `UPSTASH_REDIS_REST_TOKEN` (or
  `KV_REST_API_URL` + `KV_REST_API_TOKEN`) are set; local dev without Redis
  keeps the existing in-memory per-instance limiter
  ([#209](https://github.com/adriannoes/asap-protocol/issues/209)).
- **Auth scope parsing (#243)**: `parse_scope` lives in `asap.auth._scope_parse` so
  `OAuth2Middleware` no longer needs an inline import to break a cycle.

### Fixed

- **CI security (`pip-audit`)**: Raised `joserfc` to `>=1.6.7,<2` (CVE-2026-48990) and added overrides for `python-engineio>=4.13.2` (CVE-2026-48802 / CVE-2026-48809) and `python-socketio>=5.16.2` (CVE-2026-48804). See [SECURITY.md](SECURITY.md).
- **Streaming correlation binding (#247)**: SSE and WebSocket streaming now require `TaskStream` chunks to echo the request envelope `id` (not an arbitrary request `correlation_id`), and streamed response payloads reject missing or mismatched `correlation_id` values.
- **SQLite write serialization (#245)**: `AsyncSqliteRepository.execute()` now holds the same `asyncio.Lock` as other write paths so concurrent writers cannot interleave on a shared connection.
- **Agent status JTI replay (#249)**: `GET /asap/agent/status` now records JWT `jti` in the replay cache (same hardening as other identity routes).
- **Registry signed manifests (#224)**: Registration paths accept signed manifest envelopes (unwrap before validation).
- **Registry validation errors (#227)**: Auto-registration maps `ManifestValidationError` to HTTP 400 instead of a 500.

### Migration

- **v2.5.1 → v2.5.2**: See [migration guide](docs/migration.md#upgrading-from-v251).
  Breaking for clients that sent unknown keys on `TaskRequest.config` /
  `CommonMetadata`. Operator auth and Redis JTI remain opt-in.

---

## [2.5.1] - 2026-06-25

**Code quality patch** — behavior-preserving refactor of the transport, storage,
auth, and integration layers accumulated through v2.5.0, plus six correctness and
security fixes. No wire-protocol, manifest schema, or public API breaking changes.
Deprecated import paths keep working via shims until v2.6.0.

### Fixed

- **`revoke_cascade` atomicity**: delegation revocation now runs in a single SQLite transaction (BEGIN→walk→COMMIT, rollback on failure); the in-memory store holds an `asyncio.Lock` across the cascade so concurrent `register_issued` can't leave children un-revoked.
- **`usage_events` schema**: one canonical DDL owner in `state/stores/sqlite.py`; both indexes created with `IF NOT EXISTS` regardless of store init order, fixing schema/query-plan divergence.
- **Host-JWT verifier**: collapsed the divergent verifiers into one `verify_host_bearer`; revoked hosts now return 403 (was 401) on every protected route, including `/asap/agent/reactivate`.
- **WebSocket OAuth2 bypass**: `/asap/ws` no longer skips `OAuth2Middleware`. In OAuth2-only deployments the WS path now requires a Bearer JWT in the handshake `Authorization` header and rejects with close code 4401 when absent or invalid. Deploys without an OAuth2 IdP are unchanged.
- **OpenAPI handler**: hoisted the inline `format_www_authenticate_asap` import to module top; `OpenAPIPathParameterError.__init__` no longer raises (validation moved to `for_missing()`/`for_invalid()` factories). `resolve_headers` stays as documented public API.
- **Client correlation binding**: the client now asserts `response.correlation_id == request.id` in `send()`, `batch()`, and the WS recv loop, raising `ProtocolCorrelationError` (previously only checked non-empty).

### Changed

- **Storage consolidation**: six SQLite backends (snapshot, metering, delegation, SLA, audit, plus the economics metering store) now subclass a shared `AsyncSqliteRepository` (`state/stores/_sqlite_base.py`) with one WAL setup, `transaction()`, `parse_iso`, and `build_where`. `server.py` uses the new `MeteringStorageBridge` directly; `metering_storage_adapter` is kept as a one-line shim.
- **`server.py` decomposition**: split into `create_*_router` factories plus `routes/{health,jsonrpc,websocket,audit}.py` (2256 → 1000 LOC); `create_app` is thin wiring. Shared request prep (`parse→auth→envelope→timestamp→nonce`) moved to `ASAPRequestHandler._prepare_request`.
- **`client.py` decomposition**: split into a `client/` package (`_core`, `_send`, `_discovery`, `_helpers`); `get_manifest`/`discover` now share `_fetch_and_cache_manifest`, and `send`'s Retry-After parser is extracted. `from asap.transport.client import ASAPClient` is unchanged.
- **`websocket.py` decomposition**: split into `asap/transport/ws/` (`codecs`, `client`, `server`, `pool`, `_recv`, `_ack`, `_dispatch`, `_actions`, `_errors`); `_make_fake_request` is gone and WS dispatches envelopes directly through `_prepare_request`. `websocket.py` stays as a re-export shim.
- **Integrations**: shared `asap/integrations/_base.py` centralizes JSON-schema→Pydantic conversion, URN-scoped skill resolve cache, and per-exception error formatting; `langchain`/`crewai`/`llamaindex`/`smolagents` slimmed to glue-only (71–80 LOC each).
- **`OAuth2Middleware` JWKS**: delegates to one shared `JWKSValidator` instead of its own cache; HTTP and WS now use the same validator and TTL (`JWKS_CACHE_TTL_SECONDS=86400`).
- **`MCPServer` tool registration**: the untyped 5-tuple is now a frozen `ToolRegistration` dataclass with per-tool capability metadata.
- **`RemoteRPCError`**: `RemoteFatalRPCError`/`RemoteRecoverableRPCError` collapse into one `RemoteRPCError` with an `is_recoverable` property; the old names stay as deprecated subclasses.
- **Identity rate limiting**: the 9 inline limiter checks now use `Depends(require_identity_limiter)`; a missing limiter returns 503 (was `AttributeError` → 500).
- **`auth/` modules**: `claims`→`jwks`/`middleware`, `utils`→`scopes`, `lifecycle`→`identity`; `WebAuthnSelfAuthVerifier` deleted (`WebAuthnVerifierImpl` satisfies the protocol directly).
- **MCP Auth Bridge**: folded `asap.adapters.mcp.*` into `asap.mcp.auth.*`; `asap.adapters.mcp` stays as a re-export shim.

### Removed

- **`asap.transport` root re-exports**: `start_periodic_cleanup`, `WebhookDelivery`, `RetryPolicy`, `compute_signature`, and other tuning/webhook internals are no longer re-exported from `asap.transport.__init__` — import them from their owning modules (`asap.transport.cache`, `asap.transport.webhook`).
- **`asap.transport.codecs` package**: inlined into `asap.transport.lambda_codec`; WS framing primitives moved to `asap.transport.ws.codecs`. No shim (grep-verified zero external callers).
- **`NonceStore.is_used` / `mark_used`**: replaced by the atomic `check_and_mark`. Custom nonce stores should switch to the one-method protocol.
- **`OpenAPIExecutionKind.ASYNC_POLLING`**: pruned; a `202 + Location` OpenAPI operation now classifies as `SYNC`.
- **Dead binary/base64 WS framing**: frames are JSON text only.

### Deprecated (remove in v2.6.0)

- `from asap.cli._compat import ...` — use `asap.cli.schemas`, `asap.schemas`, or
  `asap.cli.repl` directly ([#242](https://github.com/adriannoes/asap-protocol/issues/242);
  removal tracked in [#275](https://github.com/adriannoes/asap-protocol/issues/275)).
- `from asap.transport.websocket import ...` — use `asap.transport.ws` directly.
- `from asap.adapters.mcp import ...` — use `asap.mcp.auth` directly.
- `RemoteFatalRPCError` / `RemoteRecoverableRPCError` — use `RemoteRPCError` + `is_recoverable`.
- `metering_storage_adapter` — use `MeteringStorageBridge`.
- `FRAME_ENCODING_BINARY` — binary framing is gone; the literal remains on the shim for import compat only.

### Migration

- **v2.5.0 → v2.5.1**: No breaking changes. Update deprecated import paths at your convenience (removed in v2.6.0). If you run an OAuth2-only deployment, note that `/asap/ws` now requires a Bearer JWT in the handshake — see [migration guide](docs/migration.md#upgrading-from-v250-to-v251).

---

## [2.5.0.1] - 2026-06-24

**Compliance package publish** — no change to `asap-protocol` Python API (remains **2.5.0** on PyPI). Git tag **`v2.5.0.1`** is **compliance-only** (not an npm TypeScript release).

### Fixed

- **PyPI `asap-compliance`**: Bump to **1.3.0** and publish the `mcp-auth-bridge` stdio MCP profile shipped in v2.5.0. The v2.5.0 tag did not upload a new compliance wheel because `release.yml` uses `skip-existing` and the version was still **1.2.0** (February 2026 artifact without MCP auth checks). Requires `asap-protocol>=2.5.0`.

---

## [2.5.0] - 2026-06-24

**MCP Auth Bridge** — ASAP Host/Agent JWT and capability grants as an **opt-in**
authorization layer for native **stdio MCP** `tools/call` (Mode A). Unprotected
`MCPServer` usage is unchanged. No wire-protocol or manifest schema breaking
changes.

### Added

- **feat(adapters): MCP Auth Bridge (`asap.adapters.mcp`)**
  - **`protect_server(server, config)`** — wraps `MCPServer` via
    `ProtectedMCPServer`; intercepts `_handle_tools_call` only (MCP-AUTH-006).
  - **`MCPAuthConfig`** (`config.py`) — `host_store`, `agent_store`,
    `capability_registry`, `tool_capability_map`, `public_tools`,
    `enforce_grants`, `jwt_extractor`, `jti_replay_cache`, `expected_audience`.
  - **Token carriage (stdio)** — Agent JWT in `tools/call` params
    `_meta.asap_agent_jwt`; optional dev-only `ASAP_AGENT_JWT` when
    `allow_env_jwt_fallback=True`.
  - **Grant enforcement** — `verify_agent_jwt` + `CapabilityRegistry.check_grant`
    with constraint validation on tool arguments; MCP-safe `asap:*` error codes
    (`asap:auth_required`, `asap:invalid_token`, `asap:capability_denied`,
    `asap:constraint_violation`).
  - **Tool → capability mapping** — explicit `tool_capability_map`, register-time
    metadata, or default identity (tool name == capability); optional startup
    validation (`validate_tools_at_startup`).
  - **Reference example** — `examples/mcp_auth_bridge/` (protected stdio server +
    client, smoke tests in `tests/examples/test_mcp_auth_bridge_example.py`).
  - **Docs** — [MCP Auth Bridge adapter](docs/adapters/mcp-auth-bridge.md);
    [MCP integration](docs/mcp-integration.md) distinguishes Mode A (native MCP +
    bridge) vs Mode B (MCP-over-ASAP envelope).
- **feat(compliance): `mcp-auth-bridge` profile** (`asap-compliance`) — stdio MCP
  release gate: auth paths, grants, constraints, manifest tools ⊆ registered
  tools (MCP-DISC-003). Requires `asap-protocol>=2.5.0`.
- **test(adapters/mcp)** — unit, grant, startup, and stdio integration coverage
  (≥90% on `asap.adapters.mcp`).

### Deferred (not in v2.5.0)

- **`hide_unauthorized_tools` / `tools/list` filtering** (MCP-MAP-004) — no
  standard JWT carriage on stdio `tools/list` today; see
  [design lock](engineering/tasks/v2.5.0/design-lock-mcp-auth-bridge.md).
- **`initialize` session-token handshake** (PRD §4.3 SHOULD) — clients pass JWT
  on each protected `tools/call` in v2.5.0.

### TypeScript

- **`@asap-protocol/mcp-auth` deferred** — MCP-TS-001..003 (HTTP/SSE Bearer middleware) are SHOULD-scope; v2.5.0 ships the Python stdio bridge as the release gate. Rationale and checklist:
  [typescript-mcp-auth-spike.md](engineering/tasks/v2.5.0/typescript-mcp-auth-spike.md);
  [backlog](engineering/tasks/v2.5.0/backlog-mcp-auth-typescript.md).
  **Note:** git tag **`v2.5.0.1`** was used for **`asap-compliance` 1.3.0** only; the npm middleware targets a **future patch** (TBD tag, e.g. `v2.5.0.2`).
  Existing `@asap-protocol/*` npm packages remain at **2.4.1** until that publish.

### Migration

- **v2.4.1 → v2.5.0**: **No breaking changes.** MCP servers without
  `protect_server` behave as before. To enforce Agent JWT + capabilities on native
  MCP, wrap your server with `protect_server` and configure grants — see
  [MCP Auth Bridge adapter](docs/adapters/mcp-auth-bridge.md).

---

## [2.4.1] - 2026-06-14

**Security hardening patch** — OAuth2 `iss`/`aud` validation, fail-closed identity
binding, web app SSRF/redirect fixes, and dependency bumps. Wire protocol and
manifest schemas are unchanged from v2.4.0.

### Security

- **OAuth2 middleware**: Validate JWT `iss` and `aud` when `ASAP_AUTH_ISSUER` / `ASAP_AUTH_AUDIENCE` (or `OAuth2Config.expected_issuer` / `expected_audience`) are set; refactored validation through `validate_jwt()` in `asap.auth.jwks`.
- **Identity binding**: Fail-closed when `manifest_id` is configured but neither custom claim nor `ASAP_AUTH_SUBJECT_MAP` allowlist matches (403 instead of warn-and-pass).
- **Web app (`apps/web`)**: Block open redirects on E2E fixture login routes via `resolveRedirectUrl`; harden SSRF on `/api/health-check` (127.0.0.0/8, DNS resolve4/6); Zod strict validation on public API query params (unknown keys return 400); rate-limit unit tests.

### Fixed

- **CI security (`pip-audit`)**: Bumped transitive pins (`langchain-core`, `langsmith`, `python-multipart`, `urllib3`, `pip`, `smolagents`) and adjusted documented `--ignore-vuln` flags (pygments + **smolagents** CVEs with no PyPI fix yet; pip ≥26.1 clears prior pip ignore). See [SECURITY.md](SECURITY.md).
- **FastAPI / Starlette**: Raised `fastapi` floor to `>=0.136.1` so `starlette>=1.0.1` resolves **PYSEC-2026-161** (Host header path injection) without a `pip-audit` ignore.

### Migration

- **v2.4.0 → v2.4.1**: Security patch — bump dependencies; verify OAuth2 issuer/audience and identity binding if already configured. See
  [migration guide](docs/migration.md#upgrading-from-v240-to-v241).

---

## [2.4.0] - 2026-05-24

**Edge-AI discovery** — Optional structured hardware and inference capability
advertising on manifests, mirrored to the Lite Registry, marketplace filters, and
TypeScript discovery types. Wire protocol and existing manifests remain
backward compatible.

### Added

- **feat(discovery): hardware and inference capability advertising**
  - **Manifest schema** (`schemas/entities/manifest.schema.json`): optional
    `capabilities.hardware` (`class`, `model`, `io`) and `capabilities.inference`
    (`modes`, `local_models` with optional self-reported throughput); closed enums;
    `additionalProperties: false` on nested objects. Example:
    `schemas/examples/shellclaw-jetson-capabilities.json`.
  - **Pydantic models** (`HardwareCapability`, `InferenceCapability`,
    `LocalModelInfo`; enums `HardwareClass`, `HardwareIoType`, `InferenceMode`).
  - **Registry mirror**: `RegistryEntry.hardware_class`, `inference_modes`,
    `hardware_io`; `derive_registry_hardware_fields()` applied in
    `auto_registration` and `process_registration` when a signed manifest is
    loaded.
  - **Discovery helpers**: `find_by_hardware_class`, `find_by_inference_mode`,
    `find_by_io` in `asap.discovery.registry`.
  - **Marketplace (web)**: Browse sidebar filters and agent detail blocks for
    hardware class, inference mode, and I/O; register docs and IssueOps template
    note manifest-derived fields.
  - **TypeScript SDK** (`@asap-protocol/client@2.4.0`): optional hardware fields
    on `RegistryEntry` with parsers in `discovery.ts`.
  - **Docs**: `docs/transport.md`, `docs/registry/auto-registration.md`,
    `docs/examples/registry-shellclaw.md`; fixtures under `tests/fixtures/`.
  - **ShellClaw marketplace onboarding (S0)**: Guide
    [docs/guides/shellclaw-registry.md](docs/guides/shellclaw-registry.md)
    (`online_check: false`, static manifest URLs, IssueOps path); validation
    fixtures `tests/fixtures/registry/shellclaw-v1.0-entry.json`.

### Changed

- **Tags vs structured fields**: `tags` (e.g. `cuda`, `jetson`) remain valid;
  registrants may migrate to structured `hardware` / `inference` when ready.

### Community feedback

- Track enum and field feedback on GitHub: [#176](https://github.com/adriannoes/asap-protocol/issues/176).

---

## [2.3.1] - 2026-05-20

**Adapter Lab I** — TypeScript-only patch: two new framework adapters on npm and
additive exports on `@asap-protocol/client`. Python `asap-protocol` core is
unchanged; no wire-protocol or migration work required for existing deployments.

### Added

- **`@asap-protocol/mastra@2.3.1`**: Expose ASAP capabilities as Mastra
  `createTool` definitions, optional `createAsapMastraAgent` wrapper, and
  streaming bridge. See [docs/integrations/mastra.md](docs/integrations/mastra.md).
  ```bash
  npm install @asap-protocol/mastra @asap-protocol/client @mastra/core zod
  ```
- **`@asap-protocol/openai-agents@2.3.1`**: Expose ASAP capabilities as OpenAI
  Agents SDK `tool()` definitions, remote-agent handoff helpers, and streaming
  bridge. See [docs/integrations/openai-agents.md](docs/integrations/openai-agents.md).
  ```bash
  npm install @asap-protocol/openai-agents @asap-protocol/client @openai/agents zod
  ```
- **Examples**: `apps/example-mastra` and `apps/example-openai-agents` (Compliance
  Harness v2 score 1.0 against the loopback provider).

### Changed

- **`@asap-protocol/client@2.3.1`**: Additive exports for adapter authors —
  public `@asap-protocol/client/adapters/shared`, `jsonSchemaForCapabilityOutput`,
  execution types, and envelope helpers. Non-breaking; existing `@2.3.0` consumers
  remain compatible.
- **CI security (`pip-audit`)**: Bumped transitive pins (`idna>=3.15`,
  `markdown>=3.10.2`, `pymdown-extensions>=10.21.3`) and documented `--ignore-vuln`
  flags for advisories with no PyPI fix yet (`PYSEC-2026-89`, `PYSEC-2025-183`,
  `PYSEC-2024-271`). See [SECURITY.md](SECURITY.md).

### Known limitations

- Adapter packages peer on pre-1.0 framework releases (`@mastra/core@^1.5.0`,
  `@openai/agents@^0.11.0`). Pin peers in production until those ecosystems
  stabilize semver.

### Migration

- **v2.3.0 → v2.3.1**: Additive release — install new packages only if you adopt
  Mastra or OpenAI Agents SDK integrations. See
  [migration guide](docs/migration.md#upgrading-from-v230-to-v231).

### Skipped (TS-only patch)

- **Python PyPI / Docker**: No changes to `asap-protocol` Python core (`2.3.0`
  remains current on PyPI). Tag `v2.3.1` ships npm adapters only; any Docker
  image retagged by CI is unchanged bytecode — do not treat it as a Python upgrade.

---

## [2.3.0] - 2026-05-04

**Adoption Multiplier** — OpenAPI-derived agents, first-class TypeScript, lean
registry auto-registration, runtime capability escalation, and opt-in
`WWW-Authenticate: ASAP` discovery challenges. Wire JSON-RPC and envelope
schemas remain backward compatible with v2.2.x.

### Added

- **OpenAPI Adapter (Python)**: `asap.adapters.openapi.create_from_openapi` maps
  OpenAPI 3.x operations to ASAP capabilities; optional `[openapi]` extra.
  See [docs/adapters/openapi.md](docs/adapters/openapi.md).
- **Official TypeScript client**: npm package `@asap-protocol/client` with
  discovery, capability helpers, streaming consumer, and optional adapters for
  Vercel AI SDK, OpenAI, and Anthropic (`packages/typescript/client/`).
- **Auto-Registration**: `POST /registry/agents` for Compliance Harness–gated
  Lite Registry submissions (optional `registry_auto_registration` on
  `create_app`). See [docs/registry/auto-registration.md](docs/registry/auto-registration.md).
- **Capability escalation**: `POST /asap/agent/request-capability` plus Python
  `ASAPClient.request_capability` / TypeScript client support for approval
  flows without re-registering the agent.
- **ASAP HTTP challenge**: `WWW-Authenticate: ASAP` with `discovery=` manifest
  URL on selected 401/403 responses; clients can record discovery metadata from
  the challenge header.

### Changed

- **Reference server**: New optional routers and middleware wiring are
  **opt-in** behind constructor flags — existing deployments keep v2.2.x
  behavior until they enable OpenAPI surfaces, auto-registration, escalation
  routes, or challenge middleware.

### Security

- **Dependency sweep (pre-tag)**: `uv run pip-audit --ignore-vuln CVE-2026-4539 --ignore-vuln CVE-2026-3219` (after `uv sync` per [SECURITY.md](SECURITY.md)) reported **no known vulnerabilities** (same ignores as CI). **`apps/web`**: `npm audit` (2026-05-04) still reports **moderate** `postcss` via `next@16.2.4` (GHSA-qx2v-qp2m-jg93); upstream fix may require a newer Next.js line — track before treating web audits as fully clean.

---

## [2.2.1] - 2026-04-21

Patch release: optional WebAuthn verification, compliance and audit CLIs, docs,
and CI baselines.

### Added

- **WebAuthn (optional extra)**: `asap-protocol[webauthn]` enables real
  registration/assertion verification when `ASAP_WEBAUTHN_RP_ID` and
  `ASAP_WEBAUTHN_ORIGIN` are set; otherwise behavior matches v2.2.0. See
  [v2.2.0 → v2.2.1 migration](docs/migration.md#upgrading-from-v220-to-v221).
- **`asap compliance-check`**: Runs Compliance Harness v2 against an agent
  `HTTP(S)` base URL; `--output {text,json}`, `--exit-on-fail`, `--timeout`,
  `--asap-version`. Documented in [docs/cli.md](docs/cli.md) and
  [docs/ci-compliance.md](docs/ci-compliance.md) (Actions example with
  `--exit-on-fail`).
- **`asap audit export`**: Exports hash-chained audit rows from SQLite or an
  in-memory store; `--verify-chain` fails on tampering. Documented in
  [docs/cli.md](docs/cli.md#asap-audit-export) and [docs/audit.md](docs/audit.md).
- **`apps/example-agent`**: Minimal installable example; CI runs Harness v2 and
  fails on score < 1.0 (regression guard).

### Security

- **`python-dotenv`**: `tool.uv.override-dependencies` pins **≥ 1.2.2** (CVE-2026-28684)
  on transitive installs.
- **`apps/web`**: Dependency updates closing npm audit findings (Next.js 16.2.4;
  `micromatch` subtree forced to `picomatch` ≥ 2.3.2).

### Changed

- **CLI package layout**: Typer subcommands live under `asap.cli` / `src/asap/cli/`
  (replacing the monolithic `cli.py` module); `asap` console script unchanged.
- **`ResolvedAgent.run()`**: Tightens the contract from "tri-branch dict[str, Any]" to
  "the TaskResponse.result dict, or empty dict if result is None". A protocol violation
  (server responds with anything other than a TaskResponse envelope) now raises
  `TypeError` so it surfaces at the call site instead of silently coercing into a dict.
  Return type annotation remains `dict[str, Any]`; no caller API change. Closes the
  deferred follow-up from v2.1 PR-73 review.
- **Dependency pins**: `cryptography`, `authlib`, `joserfc`, `pyjwt`, `webauthn` (extra),
  and `pydantic` now carry explicit upper bounds to the next major version so breaking
  upstream releases can't auto-install. Policy + bump procedure documented in
  [SECURITY.md §Dependency policy](SECURITY.md#dependency-policy).
- **ID generation**: `asap.state.stores.sqlite`, `asap.economics.storage`,
  `asap.economics.sla`, and `asap.integrations.a2h` now generate entity IDs through
  `asap.models.ids.generate_id()` (ULID) instead of ad-hoc `uuid.uuid4()`. The
  slowapi storage-URI suffix in `asap.transport.rate_limit` remains `uuid.uuid4().hex`
  because it is a backend namespace, not a domain identifier; an inline comment
  documents the distinction.
- **`asap.economics.storage` aggregate dispatch**: The six-branch `cast(list[UsageAggregate], ...)`
  clusters in `InMemoryMeteringStorage.aggregate` and `SQLiteMeteringStorage.aggregate` are
  consolidated into a single `_dispatch_aggregate(events, group_by)` helper. Eight
  scattered casts become three, all documented in one place.
- **`asap.transport.server` helper types**: `_validate_envelope` and `_dispatch_to_handler`
  now return a discriminable `EnvelopeOrError = JSONResponse | tuple[Envelope, str]`
  union instead of `tuple[Envelope | None, JSONResponse | str]`. Callers narrow via
  `isinstance(result, JSONResponse)` and no longer need `cast()` at the three call
  sites. Internal-only refactor; public API unchanged.
- **`WebAuthnVerifierImpl.start_webauthn_*`**: Returns the full
  `PublicKeyCredentialCreation/RequestOptions` dict from
  `webauthn.helpers.options_to_json_dict` (previously a bare base64url
  challenge). Integrators building a browser adapter get `rp`, `user`,
  `pubKeyCredParams`, `allowCredentials`, and `userVerification` out of the
  box and no longer need to hand-assemble the options envelope.
- **`default_webauthn_verifier()`**: The returned verifier is now cached
  per-process (keyed by `extra_installed / rp_id / origin`). Pending
  WebAuthn challenges persist across requests when the defensive fallback in
  `agent_routes._webauthn_verifier` is hit — previously each fallback call
  rebuilt an empty `InMemoryWebAuthnCredentialStore`, silently discarding
  any `start_webauthn_assertion` state. Tests can reset the cache with
  `asap.auth.self_auth.reset_default_webauthn_verifier_cache()`.

### Fixed

- **Swallowed exceptions in `finish_webauthn_assertion`**: Bare
  `except Exception:` blocks now catch the specific
  `InvalidAuthenticationResponse` / `ValueError` / `TypeError` classes and
  emit structured warnings (`asap.webauthn.assertion.invalid`,
  `.malformed_challenge`, `.challenge_mismatch`, `.unknown_credential`)
  via `asap.observability.get_logger` so SIEM rules can detect
  cloned-authenticator and replay patterns.
- **`WebAuthnCeremonyError` payload**: Public `detail` is now a stable,
  PII-free identifier (`webauthn_registration_state_missing`,
  `webauthn_registration_verification_failed`). `host_id` and the upstream
  library reason are logged internally instead of leaking into the error
  surface.
- **`audit export --verify-chain`**: Replaces fragile string-matching on
  `"AUDIT_CHAIN_BROKEN"` with a dedicated `asap.economics.audit.AuditChainBroken`
  exception. Invalid `--since` / `--until` ISO-8601 values now surface as
  `typer.BadParameter` instead of an unhandled traceback.
- **CSV export determinism**: `asap audit export --format csv` now writes
  rows with `lineterminator="\n"` for reproducible diffs on Linux CI.
- **Test fixtures**: `tests/cli/test_compliance_check.py` uvicorn fixtures
  now stop the server cooperatively via `server.should_exit = True` +
  `thread.join`, preventing the leaked-socket flakes flagged under
  `pytest-xdist` on long suites.

### Known limitations

- **No reference HTTP enrollment route** for the WebAuthn registration
  ceremony. `WebAuthnVerifierImpl.start_webauthn_registration` /
  `.finish_webauthn_registration` are Python-only helpers in v2.2.1;
  integrators must expose their own adapter route (see
  [docs/security/self-authorization-prevention.md](docs/security/self-authorization-prevention.md#known-limitation-no-reference-http-enrollment-route-in-v221)).
  A first-class `POST /asap/agent/webauthn/register/{begin,finish}` is
  tracked for v2.3 (Adoption Multiplier), where new endpoint surface area
  is permitted.

---

## [2.2.0] - 2026-04-15

Protocol Hardening release: per-runtime agent identity, capability-based authorization, approval flows, SSE streaming, structured error taxonomy, version negotiation, and async state stores.

### Added

#### Per-Runtime Agent Identity (S1, ADR-17 extension)
- **Host/Agent JWT**: `create_host_jwt`, `create_agent_jwt`, `verify_host_jwt`, `verify_agent_jwt` — Ed25519 (EdDSA) JWTs with `jti` replay cache (`auth/agent_jwt.py`)
- **Identity stores**: `HostIdentity`, `AgentSession`, `HostStore`, `AgentStore`, `InMemoryHostStore`, `InMemoryAgentStore` — per-host and per-agent identity with JWK thumbprint (RFC 7638) (`auth/identity.py`)
- **Agent endpoints**: `POST /asap/agent/register`, `GET /asap/agent/status`, `POST /asap/agent/revoke`, `POST /asap/agent/rotate-key` — Host JWT Bearer authentication (`transport/agent_routes.py`)

#### Capability-Based Authorization (S1)
- **Capability system**: `CapabilityDefinition`, `CapabilityGrant`, `CapabilityRegistry`, `validate_constraints` — grants with constraints (`max`, `min`, `in`, `not_in`) (`auth/capabilities.py`)
- **Capability endpoints**: `GET /asap/capability/list`, `GET /asap/capability/describe`, `POST /asap/capability/execute`, `POST /asap/agent/reactivate` (`transport/capability_routes.py`)

#### Agent Lifecycle (S1)
- **Session lifecycle**: `check_agent_expiry`, `extend_session`, `reactivate_agent` — TTL-based session management (`auth/lifecycle.py`)

#### Approval Flows (S2)
- **Device Authorization & CIBA**: `create_device_authorization`, `create_ciba_approval`, `select_approval_method`, `ApprovalStore`, `InMemoryApprovalStore` — RFC 8628 / CIBA-style approval (`auth/approval.py`)
- **A2H channel**: `A2HApprovalChannel` for agent-to-human approval resolution

#### Self-Auth Prevention (S2)
- **Fresh session**: `FreshSessionConfig`, `check_fresh_session`, `fresh_session_violation_detail` — time-windowed Host JWT validation (`auth/self_auth.py`)
- **WebAuthn**: Optional `WebAuthnVerifier` for high-risk capability registration

#### SSE Streaming (S3)
- **SSE endpoint**: `POST /asap/stream` returns `text/event-stream` with Envelope JSON events (`transport/server.py`)
- **TaskStream payload**: Streaming chunks with `chunk`, `progress`, `final`, `status` fields (`models/payloads.py`)
- **Client streaming**: `ASAPClient.stream(envelope)` parses SSE events into Envelope objects (`transport/client.py`)
- **Streaming handlers**: `HandlerRegistry.register_streaming_handler`, `dispatch_stream_async` for async generator handlers (`transport/handlers.py`)

#### Error Taxonomy (S3, PRD §4.7)
- **Error hierarchy**: `RecoverableError` / `FatalError` base classes with taxonomy URIs and JSON-RPC codes (-32000 to -32059) (`errors.py`)
- **Recovery hints**: `retry_after_ms`, `alternative_agents`, `fallback_action` on all ASAP errors
- **Remote errors**: `RemoteFatalRPCError`, `RemoteRecoverableRPCError` for client-side error reconstruction
- **Error code registry**: Documented in `docs/error-codes.md`

#### ASAP-Version Negotiation (S4)
- **Version header**: `ASAP-Version` request/response header for wire-level version negotiation (`models/constants.py`)
- **Version middleware**: `ASAPVersionMiddleware` validates version on `POST /asap` and `POST /asap/stream`; returns `VERSION_INCOMPATIBLE` JSON-RPC error for unsupported versions (`transport/middleware.py`)
- **Client negotiation**: `ASAPClient` sends supported versions and tracks `last_response_asap_version` (`transport/client.py`)
- **Manifest field**: `Manifest.supported_versions` for discovery-time version advertisement

#### Async State Stores (S4)
- **AsyncSnapshotStore**: Async protocol (`save`, `get`, `list_versions`, `delete`) replacing sync `SnapshotStore` (deprecated) (`state/snapshot.py`)
- **AsyncMeteringStore**: Async protocol (`record`, `query`, `aggregate`) replacing sync `MeteringStore` (deprecated) (`state/metering.py`)
- **SQLite async**: `SQLiteAsyncSnapshotStore` with WAL mode and pragma cache (`state/stores/sqlite.py`)

#### JSON-RPC Batch Operations (S5)
- **Server-side batch**: `POST /asap` accepts JSON arrays per JSON-RPC 2.0 batch spec; processes each request independently and returns array of responses (`transport/server.py`)
- **Batch size limit**: Configurable `max_batch_size` (default 50); oversized batches rejected with `INVALID_REQUEST` error
- **Rate limit integration**: `ASAPRateLimiter.check_n()` counts batch sub-requests against rate limits (`transport/rate_limit.py`)
- **Client batch**: `ASAPClient.batch(envelopes)` sends single HTTP request with JSON array body (`transport/client.py`)

#### Tamper-Evident Audit Logging (S5)
- **Audit models**: `AuditEntry` with SHA-256 hash chain (`prev_hash` → `hash`) for tamper detection (`economics/audit.py`)
- **AuditStore protocol**: `append`, `query`, `verify_chain`, `count` — with `InMemoryAuditStore` and `SQLiteAuditStore` implementations
- **Audit hooks**: Optional `audit_store` parameter in `create_app`; automatic logging of successful message processing
- **Audit API**: `GET /audit` with `urn`, `start`, `end`, `limit`, `offset` query parameters

#### Compliance Harness v2 (S5)
- **Harness runner**: `run_compliance_harness_v2(app)` validates ASGI applications against v2.2 spec (`testing/compliance.py`)
- **Check categories**: Identity, streaming, errors, versioning, batch, audit — each with multiple checks
- **Compliance report**: `ComplianceReport` with score (0.0–1.0), check results, and JSON export via `to_json()`

### Security

- **Dependency upgrades**: pillow 12.2.0 (CVE-2026-40192), pytest 9.0.3 (CVE-2025-71176), python-multipart 0.0.26 (CVE-2026-40347), langsmith 0.7.32 (GHSA-rr7j-v2q5-chgv)
- **Self-auth prevention**: Agents cannot register capabilities without fresh host session and optional WebAuthn verification
- **JTI replay cache**: Prevents JWT replay on mutating agent identity endpoints

### Changed

- **Wire version**: Default transport version bumped from `2.1` to `2.2`; backward compatible with `2.1`
- **State stores**: `SnapshotStore` and `MeteringStore` sync protocols deprecated in favor of async variants

### Technical Details

- **Python**: 3.13+
- **Tests**: 2941 passed, 7 skipped; full CI green (ruff, mypy, pytest, pip-audit)
- **Coverage**: ≥90% for new v2.2 modules
- **Full Changelog**: https://github.com/adriannoes/asap-protocol/compare/v2.1.1...v2.2.0

---

## [2.1.1] - 2026-03-01

Patch release addressing tech debt and findings from the v2.1.0 Red-Team code review. Backward compatible with v2.1.0.

### Security

- **SEC-01 — JWT algorithm allowlist**: `jose_jwt.decode()` in `auth/jwks.py` and `auth/middleware.py` now restricts algorithms to `EdDSA`, `RS256`, `ES256`, preventing algorithm confusion attacks (RFC 8725 §3.2).
- **Delegation `aud` claim**: RFC 7519 allows `aud` as an array; `economics/delegation.py` now coerces list to first element instead of string repr.
- **Vercel AI router**: SECURITY WARNING in docstring and optional `api_key_header` parameter for auth.
- **Frontend SSRF**: Agent registration URL validation now resolves DNS and blocks private/loopback IPs (DNS rebinding protection).

### Architecture & Concurrency

- **ARCH-01 — SQLite async bridging**: `state/stores/sqlite.py` exposes `save_async`, `get_async`, `list_versions_async`, `delete_async` using aiosqlite directly, avoiding `_run_sync` and event-loop blocking under concurrent load.
- **CONC-01**: Replaced `threading.Lock` with `asyncio.Lock` in `auth/oidc.py` and `auth/jwks.py` for cache guards so the event loop is not blocked.
- **SQLite WAL mode**: `journal_mode=WAL` and `synchronous=NORMAL` applied when opening connections in snapshot store and economics storage for better concurrency.
- **Registry locks**: GIL-atomic `dict.setdefault()` for registry URL locks in `discovery/registry.py` (removed threading guard).

### Reliability & Limits

- **Webhook dead letters**: Capped at `MAX_DEAD_LETTERS` (1000) to prevent unbounded memory growth.
- **ManifestCache**: Background cleanup hook / periodic `cleanup_expired()` documented and ensured for memory release.
- **InMemoryNonceStore**: Cleanup probability increased; optional max-size fallback.
- **Rate limiting**: Optional Redis backend (`ASAP_RATE_LIMIT_BACKEND=redis://...`) for shared limits across workers.

### Code Quality

- **echo_handler**: `TaskRequest.model_validate(envelope.payload_dict)` instead of `**payload_dict` so Pydantic validators run.
- **Registry**: Empty-string coercion for `repository_url`/`documentation_url` (strip then None).
- **SQLiteMeteringStorage**: `_ensure_table_once` so schema is created once per instance.
- **Middleware docstring**: Backlog reference updated (v2.1.1).
- **Compression**: `prefer_fast_compression` option to prefer gzip over brotli for lower latency.

### Technical Details

- **Full Changelog**: https://github.com/adriannoes/asap-protocol/compare/v2.1.0...v2.1.1

---

## [2.1.0] - 2026-02-28

Consumer SDK, framework integrations, registry UX, agent revocation, and PyPI distribution. Backward compatible with v2.0.0.

### Added

#### Consumer SDK
- **MarketClient**: Client for resolving agent URNs and invoking tasks against the registry
- **ResolvedAgent**: Resolved agent info (endpoint, manifest) for programmatic use

#### Framework Integrations
- **LangChain**: `LangChainAsapTool` (optional `[langchain]`), `langchain-core>=0.2`
- **CrewAI**: `CrewAIAsapTool` (optional `[crewai]`), `crewai>=0.80`
- **LlamaIndex**: `LlamaIndexAsapTool` (optional `[llamaindex]`), `llama-index-core>=0.10`
- **SmolAgents**: `SmolAgentsAsapTool` (optional `[smolagents]`), `smolagents>=1.0`
- **Vercel AI SDK**: Documented integration pattern
- **MCP**: Optional `[mcp]` dependency, `mcp>=1.0.0`
- **OpenClaw**: Optional `[openclaw]` with `openclaw-sdk>=2.0`; Python bridge `OpenClawAsapBridge`; Node.js skill `packages/asap-openclaw-skill` (`asap_invoke` tool); guide `docs/guides/openclaw-integration.md`

#### Registry UX
- **Categories and tags** for agents in the registry and web app
- **Usage snippets** on agent detail (including OpenClaw tab)

#### Agent Revocation
- **Revocation flow**: Agents can be revoked; clients handle `AgentRevokedException` and signature verification errors

#### PyPI Distribution
- **PyPI**: Package publishable via tag push (`v*`); `pip install asap-protocol` and `pip install asap-protocol[<extra>]` for mcp, langchain, crewai, llamaindex, smolagents, openclaw
- **CI**: Release workflow builds and publishes to PyPI on tag (Trusted Publishing)

### Technical Details

- **Python**: 3.13+
- **Full Changelog**: https://github.com/adriannoes/asap-protocol/compare/v2.0.0...v2.1.0

---

## [0.1.0] - 2026-01-23

First alpha release of the ASAP Protocol Python implementation.

### Added

#### Security & Policy (Sprint 6)
- **SECURITY.md**:
  - Updated to use GitHub Private Vulnerability Reporting
  - Clarified supported versions and reporting workflow
  - Added template for vulnerability reports
- **CODE_OF_CONDUCT.md**:
  - Simplified to a "human-readable" version focusing on respect and inclusivity
  - Removed excessive legalistic text while maintaining core standards
- **Project Structure**:
  - Improved `.gitignore` with better coverage for Python/tooling artifacts
  - Cleaned up and migrated `.cursor/commands` to `.mdc` format
  - Standardized project documentation

#### Core Models (Sprint 1)
- `ASAPBaseModel` base class with frozen config and extra forbid
- ULID-based ID generation helpers (`generate_id`, `generate_task_id`, etc.)
- Entity models: `Agent`, `Manifest`, `Conversation`, `Task`, `Message`, `Artifact`, `StateSnapshot`
- Part models: `TextPart`, `DataPart`, `FilePart`, `ResourcePart`, `TemplatePart`
- Payload models: `TaskRequest`, `TaskResponse`, `TaskUpdate`, `TaskCancel`, `MessageSend`
- MCP integration payloads: `McpToolCall`, `McpToolResult`, `McpResourceFetch`, `McpResourceData`
- `Envelope` model with auto-generated ID and timestamp
- JSON Schema export for all 24 model types

#### State Management (Sprint 2)
- `TaskStatus` enum with 8 states (submitted, working, input_required, paused, completed, failed, cancelled, rejected)
- `TaskStateMachine` with valid transition rules and validation
- `SnapshotStore` interface with `InMemorySnapshotStore` implementation
- `InvalidTransitionError` exception for state machine violations

#### HTTP Transport (Sprint 3)
- FastAPI server with `POST /asap` endpoint for JSON-RPC 2.0 messages
- `GET /.well-known/asap/manifest.json` for agent discovery
- `HandlerRegistry` for extensible payload processing
- `ASAPClient` async HTTP client with retry logic and idempotency
- JSON-RPC 2.0 request/response models with proper error codes

#### End-to-End Integration (Sprint 4)
- Example `echo_agent.py` that echoes input as output
- Example `coordinator.py` that orchestrates task requests
- `run_demo.py` script for running two-agent demonstration
- E2E test suite validating full agent-to-agent communication

#### Documentation & Polish (Sprint 5)
- Comprehensive docstrings for all public classes and methods
- MkDocs site with API reference documentation
- README with quick start guide and examples
- CLI commands: `asap --version`, `export-schemas`, `list-schemas`, `show-schema`

#### Production Readiness (Sprint 6)
- **Documentation extensions**:
  - `docs/security.md` - Auth schemes, request signing, TLS guidance
  - `docs/state-management.md` - Task lifecycle, snapshots, versioning
  - `docs/transport.md` - HTTP/JSON-RPC binding details
  - `docs/migration.md` - A2A/MCP to ASAP transition guide
- **Observability**:
  - `GET /asap/metrics` endpoint in Prometheus format
  - `MetricsCollector` with request counters and latency histograms
  - `docs/metrics.md` with usage examples
- **Tooling**:
  - `asap validate-schema [file]` command for JSON validation
  - Auto-detection of envelope schema type
  - Detailed validation error messages
- **Benchmarks**:
  - `benchmarks/` directory with pytest-benchmark tests
  - Model serialization/deserialization benchmarks
  - HTTP transport latency and throughput benchmarks

### Technical Details

- **Python**: Requires Python 3.13+
- **Dependencies**: Pydantic 2.12+, FastAPI 0.124+, httpx 0.28+, structlog 24.1+
- **Type Safety**: Full mypy strict mode compliance
- **Test Coverage**: 415+ tests with comprehensive coverage
- **Linting**: Ruff for linting and formatting

## [0.3.0] - 2026-01-26

### Changed

#### Test Infrastructure Refactoring (PR #18)
- **Test Organization**:
  - Reorganized test structure with clear separation between unit, integration, and E2E tests
  - Created `tests/transport/unit/` for isolated unit tests
  - Created `tests/transport/integration/` for integration tests with proper isolation
  - Created `tests/transport/e2e/` for end-to-end tests
- **Test Stability**:
  - Fixed 33 failing tests caused by `slowapi.Limiter` global state interference
  - Implemented process isolation using `pytest-xdist` to prevent test interference
  - Added aggressive monkeypatch strategy for complete rate limiter isolation
  - Separated rate-limiting tests from core server tests to prevent cross-contamination
- **Documentation**:
  - Added comprehensive testing guide in `docs/testing.md`
  - Documented test organization strategy and isolation techniques
  - Added examples for writing unit, integration, and E2E tests

### Fixed
- Resolved `UnboundLocalError` in `server.py` related to rate limiter initialization
- Fixed test flakiness caused by global state persistence across test runs
- Improved test reliability with proper fixture isolation

### Technical Details
- **Test Count**: 578 tests (all passing)
- **Test Execution**: Process isolation via `pytest-xdist` for complete state separation
- **Test Coverage**: Maintained comprehensive coverage across all modules

## [0.5.0] - 2026-01-28

Security-hardened release with comprehensive authentication, DoS protection, replay attack prevention, and secure logging.

**Version History**: This release builds upon v0.1.0 (initial alpha) and v0.3.0 (test infrastructure improvements). All features from previous versions are preserved with zero breaking changes.

### Added

#### Security Features (Sprint S1-S4)
- **Authentication**: Bearer token authentication with configurable token validators
  - `AuthenticationMiddleware` for request authentication
  - `BearerTokenValidator` for token validation
  - Sender verification to ensure authenticated requests match envelope sender
  - Support for multiple authentication schemes via `AuthScheme` model
- **Replay Attack Prevention**:
  - Timestamp validation with 5-minute window (`MAX_ENVELOPE_AGE_SECONDS`)
  - Optional nonce validation for replay attack prevention (`require_nonce=True`)
  - `InMemoryNonceStore` for nonce tracking with configurable TTL (10 minutes)
  - Empty nonce string validation with clear error messages
- **DoS Protection**:
  - Rate limiting (100 requests/minute per sender, configurable)
  - Request size limits (10MB default, configurable via `max_request_size`)
  - Thread pool bounds via `BoundedExecutor` to prevent resource exhaustion
  - Circuit breaker pattern for client-side failure handling
- **HTTPS Enforcement**: Client-side HTTPS validation in production mode (`require_https=True`)
- **Secure Logging**: Automatic sanitization of sensitive data in logs
  - `sanitize_token()`: Masks full tokens, shows only prefix
  - `sanitize_nonce()`: Truncates nonces in error logs
  - `sanitize_url()`: Masks credentials in connection URLs
- **Input Validation**: Enhanced validation with strict schema enforcement

#### Retry Logic (Sprint S4)
- Exponential backoff with jitter for automatic retry on transient failures
- Configurable retry parameters (`max_retries`, `base_delay`, `max_delay`, `jitter`)
- `Retry-After` header support for rate-limited responses
- Circuit breaker integration for production deployments

#### Code Quality Improvements
- Removed all `type: ignore` suppressions, achieved full mypy strict compliance (Issue #10)
- Refactored `handle_message` into smaller, testable helper functions (Issue #9)
- Enhanced type safety with explicit `None` checks and proper error handling

#### Testing & Coverage
- Comprehensive test coverage: 753 tests with 95.90% coverage
- Compatibility tests for v0.1.0 and v0.3.0 upgrade paths
- Security-focused test suites for authentication, rate limiting, and validation
- Automated upgrade test scripts for version compatibility verification

#### Documentation
- Updated `docs/security.md` with comprehensive security guidance
- Enhanced `docs/transport.md` with retry configuration and circuit breaker documentation
- Added security features to README "Why ASAP?" section
- Created compatibility test documentation

### Changed

- **FastAPI Upgrade**: Upgraded from 0.124 to 0.128.0+ (Issue #7)
- **Dependency Monitoring**: Configured Dependabot for automated security updates
- **Nonce TTL**: Made configurable, derived from `MAX_ENVELOPE_AGE_SECONDS` constant (2x envelope age)
- **Rate Limiting**: Enabled by default (100/minute) with per-sender tracking
- **Request Size Limits**: Enabled by default (10MB) with configurable limits

### Security

- **Zero Breaking Changes**: All security features are opt-in for backward compatibility
- **Security Audit**: Passed `pip-audit` (no known vulnerabilities) and `bandit` (all issues addressed)
- **Log Sanitization**: Prevents sensitive data leakage in logs (Issue #12)
- **Test Coverage**: Achieved ≥95% coverage on security-critical modules (Issue #11)

### Fixed

- Fixed empty nonce string validation (now rejects with clear error message)
- Enhanced error handling for invalid Content-Length headers
- Improved exception handling in authentication middleware
- Fixed circuit breaker persistence across client instances (Red Team remediation)

### Technical Details

- **Python**: Requires Python 3.13+
- **Dependencies**: Pydantic 2.12.5+, FastAPI 0.128.0+, httpx 0.28.1+, structlog 24.1+
- **Type Safety**: Full mypy strict mode compliance
- **Test Coverage**: 753 tests with 95.90% overall coverage
- **Linting**: Ruff for linting and formatting, all checks passing
- **Issues Closed**: #7, #9, #10, #11, #12, #13

## [Unreleased]

### Added
- Future changes will be documented here

---

## [2.0.0] - 2026-02-23

Lean Marketplace: Web App, Lite Registry, Verified Badge, IssueOps. Major release with production-ready agent discovery and registration flow.

### Added

#### Web App (Next.js 15)
- **Landing page**: Hero, value prop, protocol features, CTA
- **Browse**: Registry browser with search/filter, 500+ agents load-tested; skeleton loading (zero CLS)
- **Agent detail**: Full agent info, skills, SLA, trust level, OG images, sitemap
- **Dashboard**: My agents, registration status (Listed, Pending, Verified)
- **OAuth2**: GitHub Sign-In for developers

#### Lite Registry & IssueOps
- **Lite Registry**: `registry.json` on GitHub Pages as sole data source; no backend API
- **IssueOps**: Web form → GitHub Issue → Action parses YAML, validates, updates registry
- **Verified Badge**: 3-tier trust (Self-signed vs Verified); Manual vetting flow
- **Remove-agent flow**: Issue with `remove-agent` label for deprecation

#### Security & Hardening
- **Ed25519**: RFC 8032 strict verification (s < l), JCS (RFC 8785) for manifest signatures
- **Bandit**: CI security scanning
- **validate-registry**: CI guardrail for `registry.json` schema
- **Concurrency**: `register-agent` workflow queues rapid registrations (`cancel-in-progress: false`)
- **Proxy**: `/api/proxy/check` for CORS-bypass reachability; SSRF prevention (HTTPS only, private IP block, rate limit)
- **SECURITY.md**: Reporting policy, scope

#### Monitoring & Polish
- **Vercel**: Speed Insights + Web Analytics
- **Debug ID**: `ASAP-{ts}-{6char}` in IssueOps logs and GitHub Issue comments
- **SEO**: Dynamic OG images, sitemap (excludes mock/loadtest agents)

#### Seed & Cold Start
- **seed_registry.py**: 120 mock agents with `online_check: false` for launch social proof

### Changed

- **AGENTS.md**: Status updated to v2.0.0
- **README**: v2.0.0 Quick Info; v2.0 marked complete in roadmap

### Technical Details

- **Python**: 3.13+
- **Web**: Next.js 15, Tailwind, Shadcn/UI
- **Load test**: Playwright browse-500.spec.ts (6/6 pass)

---

## [1.4.0] - 2026-02-19

Resilience & Scale: Type safety hardening and storage pagination. Backward compatible with v1.3.0.

### Added

#### Type Safety (S1)
- **TaskRequest**: `TaskRequestConfig` model for `config` (timeout_seconds, priority, idempotency_key, streaming, persist_state, model, temperature)
- **TaskResponse**: `TaskMetrics` model for `metrics` (duration_ms, tokens_in, tokens_out, tokens_used, api_calls)
- **Entities**: `CommonMetadata` model for conversation metadata (purpose, ttl_hours, source, timestamp, tags; extra allowed)
- **Envelope**: Payload typed as discriminated union; validator parses by `payload_type`; `payload_dict` property for backward compatibility

#### Storage Pagination (S2)
- **SLAStorage**: `query_metrics(..., limit, offset)` and `count_metrics(...)`; `GET /sla/history?limit=&offset=` with `total` in response
- **MeteringStorage**: `query(..., limit, offset)`; `GET /usage?limit=&offset=` with paginated results
- **APIs**: SLA history and Usage endpoints accept `limit` (default 100, max 1000) and `offset`; responses include `count` and (SLA) `total`

#### Examples
- **v1.4.0 Showcase**: `uv run python -m asap.examples.v1_4_0_showcase` — demonstrates pagination on Usage and SLA history APIs

### Changed

- **README**: v1.4.0 Quick Info and showcase command; v1.4 (Resilience & Scale) marked complete in roadmap
- **AGENTS.md**: Status updated to v1.4.0

### Technical Details

- **Python**: 3.13+
- **Tests**: 2335+ passing; type checker (mypy) and full test suite verified
- **Coverage**: Maintained

---

## [1.3.0] - 2026-02-18

Economics Layer: Observability Metering, Delegation Tokens, and SLA Framework.
Backward compatible with v1.2.1.

### Added

#### Observability Metering (E1)
- **Usage tracking**: `MeteringStorage` protocol, `InMemoryMeteringStorage`, `SQLiteMeteringStorage`
- **Usage API**: `GET /usage`, `/usage/aggregate`, `/usage/summary`, `/usage/agents`, `/usage/consumers`, `/usage/stats`, `/usage/export`; `POST /usage`, `/usage/batch`, `/usage/purge`, `/usage/validate`
- **Task metrics**: Tokens, duration, API calls recorded per task via `MeteringStore` adapter
- **Integration**: Middleware records usage on task completion; `create_app(metering_storage=...)`

#### Delegation Tokens (E2)
- **Delegation model**: `DelegationToken`, `DelegationConstraints` (max_tasks, expires_at); JWT with EdDSA
- **Delegation API**: `POST /asap/delegations` (create token), `DELETE /asap/delegations/{id}` (revoke)
- **Storage**: `DelegationStorage` protocol, `InMemoryDelegationStorage`, `SQLiteDelegationStorage`; revocation with cascade
- **CLI**: `asap delegation create`, `asap delegation revoke`
- **Validation**: `validate_delegation`, scope checks, max_tasks enforcement, revocation lookup

#### SLA Framework (E3)
- **SLA schema**: `SLADefinition` in manifest (availability, max_latency_p95_ms, max_error_rate, support_hours)
- **SLA metrics**: `SLAMetrics`, `SLABreach`; `SLAStorage` protocol, `InMemorySLAStorage`, `SQLiteSLAStorage`
- **Breach detection**: `BreachDetector`, `evaluate_breach_conditions`; callback + WebSocket broadcast
- **SLA API**: `GET /sla`, `/sla/history`, `/sla/breaches`
- **WebSocket**: `sla.subscribe` / `sla.unsubscribe` for real-time breach notifications
- **Showcase**: `asap.examples.v1_3_0_showcase` — Delegation, Metering, SLA in one command

### Changed

- **README**: v1.3.0 showcase command; Economics Layer marked complete in roadmap
- **create_app**: `metering_storage`, `delegation_key_store`, `delegation_storage`, `sla_storage` parameters

### Technical Details

- **Python**: 3.13+
- **Tests**: 2200+ passing; cross-feature integration tests (SLA + Metering + Delegation + Health)
- **Coverage**: >95%

---

## [1.2.1] - 2026-02-15

Security remediation pre-v1.3.0. Critical JWT fix + hardening. Backward compatible with v1.2.0.

### Fixed

- **JWT exp validation**: Reject expired tokens in `validate_jwt` to prevent authentication bypass (P0)
- **WebSocket**: Reraise critical exceptions (`SystemExit`, `KeyboardInterrupt`) in heartbeat loop
- **OIDC SSRF**: Validate `issuer_url` to block private/internal hosts; `allow_private_issuers=True` for dev/test

### Security

- **MCP**: Document trusted-source requirement for `server_command`; add opt-in `allowed_binaries` validation
- **OIDC**: Block 127.0.0.1, 10.x, 172.16–31.x, 192.168.x by default

### Changed

- **SQLite**: Use shared executor for sync bridge (performance; no per-call ThreadPoolExecutor)

### Added

- **Tests**: Introspection cache eviction, WebSocket SSL context, WebSocket race condition (close-during-connect), MCP allowlist, SQLite thread count
- **Docs**: Security remediation plan, P4.2 loose-typing follow-up task

---

## [1.2.0] - 2026-02-15

Verified Identity: Ed25519 signed manifests, trust levels, optional mTLS, and compliance harness.
Backward compatible with v1.1.0.

### Added

#### Signed Manifests (T1, SD-4, SD-5)
- **Ed25519 key management**: `asap.crypto.keys` — `generate_keypair`, `serialize_private_key`, `load_private_key_from_file_sync`, `load_private_key_from_env`
- **Manifest signing**: `asap.crypto.signing` — `sign_manifest`, `verify_manifest`, JCS canonicalization (RFC 8785)
- **SignedManifest model**: `asap.crypto.models` — `SignedManifest`, `SignatureBlock` with `public_key` and `trust_level`
- **CLI**: `asap keys generate`, `asap manifest sign`, `asap manifest verify`, `asap manifest info`

#### Trust Levels (T2, SD-5)
- **Trust level model**: `asap.crypto.trust_levels` — `TrustLevel` enum (self-signed, verified, enterprise)
- **Trust detection**: `asap.crypto.trust` — `detect_trust_level`, `sign_with_ca` for Verified badge simulation
- **Client verification**: `ASAPClient` — `verify_signatures`, `trusted_manifest_keys` for optional manifest signature verification

#### mTLS (T2, SD-6)
- **Optional mTLS**: `asap.transport.mtls` — `MTLSConfig`, `create_ssl_context`, `mtls_config_to_uvicorn_kwargs`
- **Server/client support**: `create_app(mtls_config=...)`, `ASAPClient(mtls_config=...)`
- **Documentation**: `docs/security/mtls.md` — Enterprise CA, client cert configuration

#### Compliance Harness (T3)
- **asap-compliance package**: Separate PyPI package for protocol compliance testing
- **Handshake validation**: Health endpoint, manifest schema, signed manifest verification, version compatibility
- **Schema validation**: Envelope, TaskRequest, TaskResponse, McpToolResult, MessageAck; `extra="forbid"`
- **State machine validation**: Task lifecycle (PENDING → RUNNING → COMPLETED/FAILED)
- **SLA validation**: Timeout and progress schema checks
- **Usage**: `pytest --asap-agent-url https://your-agent.example.com -m asap_compliance`

#### Testing & Benchmarks (T4)
- **Cross-version compatibility**: Signed manifests with discovery; compliance harness against signed manifest agents
- **Crypto benchmarks**: `benchmarks/benchmark_crypto.py` — Ed25519 sign/verify, JCS canonicalization, compliance handshake
- **Coverage**: CLI keys/manifest tests, mTLS edge cases, integration tests

### Changed

- **Discovery validation**: `validate_signed_manifest_response` accepts plain or signed manifests; optional signature verification
- **AGENTS.md**: mTLS note updated (now implemented); crypto module and compliance harness documented

### Deferred (not in v1.2.0)

- **Registry API**: Centralized agent registry backend (planned for v2.1)
- **DeepEval integration**: Intelligence layer for compliance (planned for v2.2+)
- **Lite Registry** (v1.1) continues as discovery mechanism

### Technical Details

- **Python**: 3.13+
- **Tests**: 1940+ (asap-protocol), 54 (asap-compliance)
- **Coverage**: ~94.2% (asap-protocol)
- **New packages**: `asap-compliance` on PyPI (separate from `asap-protocol`)

---

## [1.0.0] - 2026-02-03

Production-ready release. All features from v0.5.0 preserved with backward compatibility (see contract tests: v0.1.0 → v1.0.0, v0.5.0 ↔ v1.0.0).

### Added

#### Security & Validation (P1–P2)
- **Log sanitization**: Credential and token patterns in logs; `ASAP_DEBUG` env var for controlled verbose output
- **Handler security**: `FilePart` URI validation, path traversal detection; `docs/security.md` updated
- **Thread safety**: Thread-safe `HandlerRegistry` for concurrent handler registration and execution
- **URN validation**: Max 256-character URNs, task depth limits, enhanced input validation
- **`ManifestCache` LRU eviction**: `max_size` limit with LRU eviction to prevent unbounded cache growth

#### Performance (P3–P4)
- **Connection pooling**: Configurable `ASAPClient` connection pooling; supports 1000+ concurrent connections
- **Manifest caching**: TTL-based manifest cache; `manifest_cache_size` parameter for LRU-bounded cache
- **Batch operations**: `send_batch` with HTTP/2 multiplexing for higher throughput
- **Compression**: Gzip and Brotli support; `Accept-Encoding` negotiation; ~70% bandwidth reduction for JSON

#### Developer Experience (P5–P6)
- **Examples**: 15+ real-world examples (auth patterns, rate limiting, state migration, streaming, multi-step workflow, MCP integration, MCP client, error recovery, long-running, orchestration)
- **Testing utilities**: `asap.testing` fixtures, factories, and helpers; reduced test boilerplate
- **Trace visualization**: `asap trace` CLI command; optional Web UI for trace parsing
- **Dev server**: Hot reload, debug logging, REPL mode for local development
- **CLI**: `trace`, `repl`, `export-schemas`, `list-schemas`, `show-schema`, `validate-schema` commands

#### Testing (P7–P8)
- **Property-based tests**: Hypothesis-based tests for models, IDs, and edge cases
- **Load testing**: Locust-based load tests; RPS and latency benchmarks
- **Chaos tests**: Failure injection, message reliability under faults
- **Contract tests**: v0.1.0 → v1.0.0 and v0.5.0 ↔ v1.0.0 upgrade paths; schema evolution and rollback coverage
- **Integration tests**: Batch + auth + pooling; MCP server + ASAP protocol; compression edge cases

#### Documentation (P9–P10)
- **Tutorials**: 5 step-by-step tutorials (beginner → advanced → DevOps)
- **ADRs**: 17 Architecture Decision Records (ULID, async-first, JSON-RPC, Pydantic, state machine, security, FastAPI, httpx, snapshot, Python 3.13, rate limiting, error taxonomy, MCP, testing, observability, versioning, failure injection)
- **Deployment**: Docker image, Kubernetes manifests, Helm chart, health probes
- **Troubleshooting**: Guide covering common issues and diagnostics

#### Observability (P11–P12)
- **OpenTelemetry**: Tracing integration with OTLP export; zero-config for development
- **Metrics**: Structured metrics (counters, histograms); Prometheus export; `asap_handler_errors_total` and transport client metrics
- **Dashboards**: Grafana dashboards (RED, detailed) for ASAP agents
- **Jaeger**: Integration test and trace export to Jaeger

#### MCP (P12)
- **MCP implementation**: Full MCP server/client; `serve_stdio`; tool call/result, resource fetch/data payloads
- **Validation**: Parse error handling, tool args validation, sanitized error responses
- **Interop**: Default `request_id_type=str` for interoperability

### Changed

- **MCP**: `request_id_type` defaults to `str` for better interoperability
- **CI**: Parallel jobs (lint, types, tests, security); path filters; coverage thresholds
- **Build**: Per-module mypy overrides; `jsonschema` dependency; pytest `pythonpath` for tests

### Fixed

- **Transport**: Narrow `JSONResponse | str` with cast for mypy in `handle_message`
- **MCP**: Parse errors, tool args validation, error sanitization; `serve_stdio` support
- **Observability**: Server import order, tracing type hints, debug logging, `reset_tracing`
- **Tests**: Jaeger handler trace detection among multiple traces; rate limiter isolation; flaky ULID sortable test
- **Handlers**: Use `get_running_loop` instead of deprecated `get_event_loop`
- **Utils**: `sanitize_url` exception fallback coverage

### Technical Details

- **Python**: 3.13+
- **Tests**: 1379+ passing; property, load, chaos, and contract test suites
- **Coverage**: ~95% (target met)
- **Linting**: Ruff (check + format), mypy strict
- **Security**: `pip-audit` clean; bandit (Low findings limited to examples and test helpers)

## [1.1.0] - 2026-02-10

Identity layer: OAuth2/OIDC, discovery, WebSocket, state storage, and webhooks. Backward compatible with v1.0.0.

### Added

#### Identity & Auth (S1, ADR-17)
- **OAuth2 server**: `OAuth2Config`, `OAuth2Middleware` — JWT validation via JWKS, optional scope, path prefix
- **OAuth2 client**: `OAuth2ClientCredentials` for client_credentials grant; `Token` model with expiry
- **OIDC discovery**: `OIDCDiscovery`, `OIDCConfig` — auto-config from `/.well-known/openid-configuration`
- **Custom Claims identity binding**: JWT custom claim (default: `https://github.com/adriannoes/asap-protocol/agent_id`; future: `https://asap-protocol.com/agent_id`) or `ASAP_AUTH_SUBJECT_MAP` allowlist; envelope sender must match authenticated agent
- **v1.1 Security Model**: `docs/security/v1.1-security-model.md` — trust limitations, Custom Claims, Auth0/Keycloak/Azure AD guides

#### Discovery (S2, SD-11, ADR-15)
- **Well-known**: `GET /.well-known/asap/manifest.json`; `ASAPClient.discover(base_url)`
- **Lite Registry**: `discover_from_registry()`, `LiteRegistry` — static JSON on GitHub Pages for agent discovery
- **Health/liveness**: `GET /.well-known/asap/health` with `ttl_seconds` in manifest (SD-10, ADR-14); `HealthStatus` model

#### State Storage (S2.5, SD-9, ADR-13)
- **MeteringStore Protocol**: Interface for usage metering (v1.3 foundation)
- **SQLiteSnapshotStore**: Persistent snapshot store via `aiosqlite`; `src/asap/state/stores/sqlite.py`
- **Storage configuration**: `ASAP_STORAGE_BACKEND` env (e.g. `memory`, `sqlite`); `create_snapshot_store()`
- **Best Practices**: `docs/best-practices/agent-failover-migration.md` — state handover, failover patterns

#### WebSocket (S3, SD-3, ADR-16)
- **WebSocket server**: ASAP messages over WebSocket; `websocket_asap` endpoint
- **WebSocket client**: `ASAPClient(transport_mode="websocket")`, `WebSocketTransport`
- **MessageAck**: Selective ack for state-changing messages; `requires_ack` on Envelope; `MessageAck` payload
- **AckAwareClient**: Pending ack tracking, timeout/retry, circuit breaker integration
- **WebSocket rate limiting**: Per-connection token bucket (default 10 msg/s)

#### Webhooks (S4)
- **WebhookDelivery**: POST callbacks to validated URLs; HMAC-SHA256 `X-ASAP-Signature`; SSRF checks (private IPs, localhost blocked; HTTPS in production)
- **WebhookRetryManager**: Retry queue, exponential backoff (1s–16s, max 5 retries), dead-letter handling, per-URL rate limit
- **`asap.transport.webhook`**: `WebhookDelivery`, `WebhookRetryManager`; `WebhookURLValidationError` in `asap.errors`

#### Transport & Infra
- **Custom rate limiter**: Migrated from slowapi to `ASAPRateLimiter` (using `limits` package); removes Python 3.12+ deprecation warnings
- **Example**: `secure_agent.py` — OAuth2Config server + OAuth2ClientCredentials client with env-based config

### Changed

- **Rate limiting**: Replaced slowapi with `ASAPRateLimiter`; `rate_limit.py`; backward-compatible `create_limiter()` / `create_test_limiter()`
- **Dependencies**: Removed `slowapi`; added `limits>=3.0`
- **InMemorySnapshotStore**: Moved to `src/asap/state/stores/memory.py`; re-exported for backward compatibility

### Technical Details

- **Python**: 3.13+
- **Tests**: 1800+ passing; property, integration, chaos, contract suites
- **Coverage**: ~94.5% (examples/dnssd omitted); 95% target in backlog
- **Docs**: v1.1 features table in `docs/index.md`; README/AGENTS.md updated; Security Model linked from README, AGENTS.md, docs

