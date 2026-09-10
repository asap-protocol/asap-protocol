# Migration Guide

> Migrating from A2A (Agent-to-Agent) and MCP (Model Context Protocol) to ASAP.

---

## Overview

ASAP (Agent State and Action Protocol) builds upon concepts from both A2A and MCP, providing a unified protocol for agent-to-agent communication. This guide helps developers migrate existing agents to ASAP.

### Protocol Comparison

| Feature | A2A | MCP | ASAP |
|---------|-----|-----|------|
| **Focus** | Agent communication | Tool/resource access | Unified agent protocol |
| **State Management** | Limited | None | First-class snapshots |
| **Message Format** | JSON | JSON-RPC | JSON-RPC + Envelope |
| **Discovery** | Agent Card | Server info | Manifest |
| **Task Lifecycle** | Basic | N/A | Full state machine |
| **Streaming** | SSE + WebSocket (`/asap/stream`, `/asap/ws`) | Stdio/SSE | SSE + WebSocket |

---

## Envelope and Payload Mapping

### A2A to ASAP

#### A2A Message Structure

```json
{
  "type": "task",
  "id": "task-123",
  "from": "agent-a",
  "to": "agent-b",
  "content": {
    "action": "research",
    "input": {"query": "AI trends"}
  }
}
```

#### ASAP Envelope Structure

```json
{
  "id": "env_01HX5K4P...",
  "asap_version": "0.1",
  "timestamp": "2024-01-15T10:30:00Z",
  "sender": "urn:asap:agent:agent-a",
  "recipient": "urn:asap:agent:agent-b",
  "payload_type": "task.request",
  "payload": {
    "conversation_id": "conv_01HX5K3M...",
    "skill_id": "research",
    "input": {"query": "AI trends"}
  },
  "trace_id": "trace_01HX5K..."
}
```

#### Key Differences

| A2A Field | ASAP Field | Notes |
|-----------|------------|-------|
| `type` | `payload_type` | More specific typing |
| `id` | `id` | ULID format in ASAP |
| `from` | `sender` | URN format required |
| `to` | `recipient` | URN format required |
| `content` | `payload` | Structured per payload type |
| *(none)* | `asap_version` | Protocol versioning |
| *(none)* | `timestamp` | Auto-generated |
| *(none)* | `trace_id` | Distributed tracing |
| *(none)* | `correlation_id` | Request/response pairing |

### MCP to ASAP

#### MCP Tool Call

```json
{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "params": {
    "name": "web_search",
    "arguments": {"query": "AI trends"}
  },
  "id": 1
}
```

#### ASAP MCP Tool Call

```json
{
  "jsonrpc": "2.0",
  "method": "asap.send",
  "params": {
    "envelope": {
      "asap_version": "0.1",
      "sender": "urn:asap:agent:coordinator",
      "recipient": "urn:asap:agent:tools-agent",
      "payload_type": "mcp.tool_call",
      "payload": {
        "request_id": "req_01HX5K...",
        "tool_name": "web_search",
        "arguments": {"query": "AI trends"},
        "mcp_context": {}
      }
    }
  },
  "id": "req-1"
}
```

#### Key Differences

| MCP Field | ASAP Field | Notes |
|-----------|------------|-------|
| `method` | `payload_type` | `mcp.tool_call` for tools |
| `params.name` | `payload.tool_name` | Tool identifier |
| `params.arguments` | `payload.arguments` | Same structure |
| *(none)* | `sender`/`recipient` | Agent identity |
| *(none)* | `mcp_context` | Additional context |

---

## Agent Card to Manifest

### A2A Agent Card

```json
{
  "name": "Research Agent",
  "description": "Performs web research",
  "url": "https://agent.example.com",
  "capabilities": ["research", "summarize"],
  "authentication": {
    "type": "bearer"
  }
}
```

### ASAP Manifest

```json
{
  "id": "urn:asap:agent:research-v1",
  "name": "Research Agent",
  "version": "1.0.0",
  "description": "Performs web research",
  "capabilities": {
    "asap_version": "0.1",
    "skills": [
      {
        "id": "research",
        "description": "Search and analyze information",
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"}
      },
      {
        "id": "summarize",
        "description": "Summarize content",
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"}
      }
    ],
    "state_persistence": true,
    "streaming": false,
    "mcp_tools": []
  },
  "endpoints": {
    "asap": "https://agent.example.com/asap",
    "events": null
  },
  "auth": {
    "schemes": ["bearer"],
    "oauth2": null
  }
}
```

### Key Improvements in ASAP

| Feature | A2A Agent Card | ASAP Manifest |
|---------|----------------|---------------|
| **Identity** | URL-based | URN-based (`urn:asap:agent:*`) |
| **Versioning** | None | Semantic versioning |
| **Skills** | String list | Structured with schemas |
| **State** | Not specified | `state_persistence` flag |
| **Endpoints** | Single URL | Multiple endpoints |
| **Validation** | None | JSON Schema for I/O |

---

## Payload Type Mapping

### Task Operations

| A2A Action | MCP Method | ASAP Payload Type |
|------------|------------|-------------------|
| `task.create` | N/A | `task.request` |
| `task.status` | N/A | `task.response` |
| `task.update` | N/A | `task.update` |
| `task.cancel` | N/A | `task.cancel` |

### MCP Operations

| MCP Method | ASAP Payload Type |
|------------|-------------------|
| `tools/call` | `mcp.tool_call` |
| `tools/call` (response) | `mcp.tool_result` |
| `resources/read` | `mcp.resource_fetch` |
| `resources/read` (response) | `mcp.resource_data` |

### Message Operations

| Operation | ASAP Payload Type |
|-----------|-------------------|
| Send message | `message.send` |
| Query state | `state.query` |
| Restore state | `state.restore` |
| Notify artifact | `artifact.notify` |

---

## Migration Checklist

### Phase 1: Preparation

- [ ] **Review current protocol usage**
  - Document all A2A/MCP endpoints in use
  - List all message types and payloads
  - Identify state management patterns

- [ ] **Plan agent identity**
  - Define URN format: `urn:asap:agent:{agent-name}`
  - Plan versioning strategy (semantic versioning)
  - Document skill definitions

- [ ] **Set up ASAP dependencies**
  ```bash
  # Install ASAP protocol library
  pip install asap-protocol
  # Or with uv
  uv add asap-protocol
  ```

### Phase 2: Manifest Creation

- [ ] **Create agent manifest**
  ```python
  from asap.models.entities import (
      Manifest, Capability, Endpoint, Skill, AuthScheme
  )
  
  manifest = Manifest(
      id="urn:asap:agent:my-agent",
      name="My Agent",
      version="1.0.0",
      description="Migrated from A2A/MCP",
      capabilities=Capability(
          asap_version="0.1",
          skills=[
              Skill(id="skill1", description="..."),
              # Add all skills
          ],
          state_persistence=True,  # Enable if needed
          streaming=False,
          mcp_tools=["tool1", "tool2"],  # MCP tools
      ),
      endpoints=Endpoint(asap="https://my-agent.example.com/asap"),
      auth=AuthScheme(schemes=["bearer"]),
  )
  ```

- [ ] **Expose manifest endpoint**
  ```python
  # Manifest will be available at:
  # GET /.well-known/asap/manifest.json
  ```

### Phase 3: Message Conversion

- [ ] **Update message sending**
  ```python
  # Before (A2A style)
  message = {"type": "task", "from": "agent-a", ...}
  response = requests.post(url, json=message)
  
  # After (ASAP)
  from asap.transport.client import ASAPClient
  from asap.models.envelope import Envelope
  
  envelope = Envelope(
      asap_version="0.1",
      sender="urn:asap:agent:agent-a",
      recipient="urn:asap:agent:agent-b",
      payload_type="task.request",
      payload={...}
  )
  
  async with ASAPClient(base_url=url) as client:
      response = await client.send(envelope)
  ```

- [ ] **Update message receiving**
  ```python
  # Use ASAP server with handler registry
  from asap.transport.server import create_app
  from asap.transport.handlers import HandlerRegistry
  
  registry = HandlerRegistry()
  registry.register("task.request", handle_task_request)
  
  app = create_app(manifest, registry)
  ```

### Phase 4: State Management

- [ ] **Implement state snapshots** (if using state persistence)
  ```python
  from asap.state.snapshot import InMemorySnapshotStore
  from asap.models.entities import StateSnapshot
  
  store = InMemorySnapshotStore()
  
  # Save state
  snapshot = StateSnapshot(
      id=generate_id(),
      task_id=task.id,
      version=1,
      data={"current_state": "..."},
      checkpoint=True,
      created_at=datetime.now(timezone.utc),
  )
  store.save(snapshot)
  
  # Restore state
  restored = store.get(task_id)
  ```

- [ ] **Use state machine for task lifecycle**
  ```python
  from asap.state.machine import transition, can_transition
  from asap.models.enums import TaskStatus
  
  # Validate and perform transition
  if can_transition(task.status, TaskStatus.COMPLETED):
      task = transition(task, TaskStatus.COMPLETED)
  ```

### Phase 5: Testing

- [ ] **Unit tests for new handlers**
  ```python
  import pytest
  from asap.transport.server import create_app
  from fastapi.testclient import TestClient
  
  def test_task_request():
      app = create_app(manifest)
      client = TestClient(app)
      
      response = client.post("/asap", json={
          "jsonrpc": "2.0",
          "method": "asap.send",
          "params": {"envelope": {...}},
          "id": "test-1"
      })
      
      assert response.status_code == 200
  ```

- [ ] **Integration tests**
  ```python
  @pytest.mark.asyncio
  async def test_full_flow():
      async with ASAPClient(base_url="http://localhost:8000") as client:
          response = await client.send(envelope)
          assert response.payload_type == "task.response"
  ```

- [ ] **Verify manifest discovery**
  ```bash
  curl http://localhost:8000/.well-known/asap/manifest.json
  ```

### Phase 6: Deployment

- [ ] **Update API documentation**
- [ ] **Configure monitoring** (trace IDs, logging)
- [ ] **Set up TLS** (required for production)
- [ ] **Configure authentication**
- [ ] **Gradual rollout**
  - Deploy ASAP endpoint alongside existing
  - Route traffic gradually
  - Monitor for errors
  - Deprecate old endpoints

---

## Common Migration Patterns

### Pattern 1: Dual-Protocol Support

Run both A2A/MCP and ASAP during transition:

```python
from fastapi import FastAPI

app = FastAPI()

# Legacy A2A endpoint
@app.post("/a2a")
async def handle_a2a(request: dict):
    # Convert to ASAP internally
    envelope = convert_a2a_to_asap(request)
    return await process_envelope(envelope)

# New ASAP endpoint
@app.post("/asap")
async def handle_asap(request: dict):
    return await process_envelope(request)
```

### Pattern 2: MCP Tool Wrapper

Wrap existing MCP tools for ASAP:

```python
from asap.transport.handlers import HandlerRegistry

def create_mcp_handler(mcp_tool_function):
    """Wrap MCP tool as ASAP handler."""
    def handler(envelope, manifest):
        tool_name = envelope.payload["tool_name"]
        arguments = envelope.payload["arguments"]
        
        # Call original MCP tool
        result = mcp_tool_function(tool_name, arguments)
        
        # Return ASAP response
        return create_tool_result_envelope(envelope, result)
    
    return handler

registry = HandlerRegistry()
registry.register("mcp.tool_call", create_mcp_handler(call_mcp_tool))
```

### Pattern 3: State Migration

Migrate existing state to ASAP snapshots:

```python
async def migrate_state(old_state_store, new_snapshot_store):
    """Migrate state from old format to ASAP snapshots."""
    for task_id, state in old_state_store.items():
        snapshot = StateSnapshot(
            id=generate_id(),
            task_id=task_id,
            version=1,
            data=state,
            checkpoint=True,
            created_at=datetime.now(timezone.utc),
        )
        new_snapshot_store.save(snapshot)
```

---

## Upgrading ASAP Protocol Versions

### Version History

ASAP Protocol has evolved through the following releases:
- **v0.1.0** (2026-01-23): Initial alpha release with core models, state management, and HTTP transport
- **v0.3.0** (2026-01-26): Test infrastructure refactoring and stability improvements
- **v0.5.0** (2026-01-28): Security-hardened release with authentication, DoS protection, and comprehensive security features

### Upgrading from v0.1.0 or v0.3.0 to v0.5.0

v0.5.0 is a **security-hardened release** with zero breaking changes. All existing code from v0.1.0 and v0.3.0 will work without modifications.

**Upgrade Paths:**
- **v0.1.0 → v0.5.0**: Direct upgrade supported (tested and verified)
- **v0.3.0 → v0.5.0**: Direct upgrade supported (tested and verified)
- **v0.1.0 → v0.3.0 → v0.5.0**: Sequential upgrade also supported

#### What's New in v0.5.0

- **Security Features** (all opt-in):
  - Bearer token authentication
  - Timestamp validation (5-minute window)
  - Optional nonce validation for replay attack prevention
  - Rate limiting (100 req/min, configurable)
  - Request size limits (10MB, configurable)
  - HTTPS enforcement (client-side)
  - Secure logging (automatic sanitization)

- **Retry Logic**:
  - Exponential backoff with jitter
  - Circuit breaker pattern
  - `Retry-After` header support

- **Code Quality**:
  - Full mypy strict compliance
  - Enhanced error handling
  - Improved test coverage (95.90%)

#### Upgrade Steps

1. **Update Dependencies**:
   ```bash
   pip install --upgrade asap-protocol==0.5.0
   # or
   uv add asap-protocol==0.5.0
   ```

2. **Verify Compatibility**:
   Your existing code should work without changes:
   ```python
   # This code from v0.1.0/v0.3.0 still works in v0.5.0
   from asap.models.entities import Manifest, Capability, Endpoint, Skill
   from asap.transport.handlers import HandlerRegistry, create_echo_handler
   from asap.transport.server import create_app
   
   manifest = Manifest(
       id="urn:asap:agent:my-agent",
       name="My Agent",
       version="1.0.0",
       description="My agent",
       capabilities=Capability(
           asap_version="0.1",
           skills=[Skill(id="echo", description="Echo skill")],
           state_persistence=False,
       ),
       endpoints=Endpoint(asap="http://127.0.0.1:8000/asap"),
   )
   
   registry = HandlerRegistry()
   registry.register("task.request", create_echo_handler())
   app = create_app(manifest, registry)
   ```

3. **Opt-in to Security Features** (optional):
   ```python
   from asap.models.entities import AuthScheme
   from asap.transport.middleware import BearerTokenValidator
   
   # Add authentication (optional)
   def validate_token(token: str) -> str | None:
       if token == "my-secret-token":
           return "urn:asap:agent:client"
       return None
   
   manifest = Manifest(
       # ... existing manifest ...
       auth=AuthScheme(schemes=["bearer"])  # Enable authentication
   )
   
   app = create_app(
       manifest,
       registry,
       token_validator=validate_token,  # Add token validator
       rate_limit="10/second;100/minute", # Burst + sustained rate limiting
       require_nonce=True                # Enable nonce validation
   )
   ```

4. **Test Your Upgrade**:
   ```bash
   # Run compatibility tests
   uv run python -m pytest tests/compatibility/ -v
   ```

#### Backward Compatibility

- ✅ **No breaking changes**: All v0.1.0 and v0.3.0 APIs remain unchanged
- ✅ **Security features are opt-in**: Existing deployments continue to work
- ✅ **Default behavior unchanged**: Rate limiting and size limits have safe defaults
- ✅ **Examples still work**: All example code from previous versions is compatible

#### Migration Checklist

- [ ] Update `asap-protocol` to v0.5.0
- [ ] Run existing tests to verify compatibility
- [ ] Review security features and opt-in as needed
- [ ] Update documentation if using new security features
- [ ] Test in staging environment before production deployment

#### What Changed Between Versions

**v0.1.0 → v0.3.0** (2026-01-26):
- Test infrastructure refactoring
- Improved test stability and isolation
- Fixed rate limiter initialization issues
- Enhanced test organization (unit/integration/E2E separation)

**v0.3.0 → v0.5.0** (2026-01-28):
- Security hardening (authentication, DoS protection, replay prevention)
- Retry logic with exponential backoff
- Secure logging with automatic sanitization
- Enhanced code quality (mypy strict compliance)
- Improved test coverage (95.90%)

#### Need Help?

- See [Security Guide](security.md) for authentication setup
- See [Transport Guide](transport.md) for retry configuration
- Run compatibility tests: `tests/compatibility/test_v0_1_0_compatibility.py`
- Guide for migrating from A2A, MCP, or older ASAP versions. See [CHANGELOG](../CHANGELOG.md) for version history.

### Upgrading from v2.1.x to v2.2.0

v2.2.0 is a **Protocol Hardening** release. All features are additive — existing v2.1.x deployments will work without changes.

#### What's New in v2.2.0

- **Per-Runtime Agent Identity** (optional): Ed25519-based Host/Agent JWT identity with registration, revocation, and key rotation endpoints
- **Capability-Based Authorization** (optional): Define capabilities with constraints, grant/deny per agent, enforce at runtime
- **Agent Lifecycle**: Session TTL, expiry checks, reactivation
- **Approval Flows**: Device Authorization (RFC 8628) and CIBA-style approval for sensitive operations
- **Self-Auth Prevention**: Fresh session windows and optional WebAuthn for high-risk registrations
- **SSE Streaming**: `POST /asap/stream` endpoint with `TaskStream` payload and client-side `stream()` method
- **Error Taxonomy**: `RecoverableError` / `FatalError` hierarchy with JSON-RPC codes (-32000 to -32059) and recovery hints
- **ASAP-Version Header**: Wire-level version negotiation on `/asap` and `/asap/stream`
- **Async State Stores**: `AsyncSnapshotStore` and `AsyncMeteringStore` protocols (sync variants deprecated)

#### Upgrade Steps

1. **Update dependency**:
   ```bash
   pip install --upgrade asap-protocol==2.2.0
   # or
   uv add asap-protocol==2.2.0
   ```

2. **Adopt ASAP-Version header** (recommended):
   The server now includes `ASAPVersionMiddleware` by default. Clients sending requests to `/asap` or `/asap/stream` should include the `ASAP-Version` header. The client does this automatically:
   ```python
   from asap.transport.client import ASAPClient

   async with ASAPClient("https://agent.example.com") as client:
       # ASAP-Version header sent automatically
       response = await client.send(envelope)
   ```

3. **Migrate to AsyncSnapshotStore** (recommended):
   ```python
   # Before (deprecated sync API)
   from asap.state.snapshot import SnapshotStore

   # After (async API)
   from asap.state.snapshot import AsyncSnapshotStore, create_async_snapshot_store

   store = create_async_snapshot_store("sqlite:///data.db")
   await store.save(snapshot)
   snapshot = await store.get(task_id)
   ```

4. **Adopt per-agent identity** (optional):
   ```python
   from asap.auth.identity import InMemoryHostStore, InMemoryAgentStore
   from asap.auth.agent_jwt import create_host_jwt
   from asap.transport.server import create_app

   host_store = InMemoryHostStore()
   agent_store = InMemoryAgentStore()

   app = create_app(
       manifest,
       registry,
       identity_host_store=host_store,
       identity_agent_store=agent_store,
   )
   ```

5. **Define capabilities** (optional):
   ```python
   from asap.auth.capabilities import CapabilityDefinition, CapabilityRegistry

   registry = CapabilityRegistry()
   registry.register(CapabilityDefinition(
       name="task.execute",
       description="Execute tasks",
       constraints_schema={"max_concurrent": {"type": "integer"}},
   ))
   ```

6. **Use streaming** (optional):
   ```python
   from asap.transport.client import ASAPClient

   async with ASAPClient("https://agent.example.com") as client:
       async for envelope in client.stream(request_envelope):
           chunk = envelope.payload  # TaskStream with progress info
           if chunk.final:
               break
   ```

#### Backward Compatibility

- ✅ **No breaking changes**: All v2.1.x APIs remain functional
- ✅ **Identity is opt-in**: Existing agents work without Host/Agent JWT
- ✅ **Version negotiation is backward compatible**: Servers accept both `2.1` and `2.2`
- ✅ **Async stores coexist**: Sync `SnapshotStore` still works (deprecated, removal in v2.3)

#### Migration Checklist

- [ ] Update `asap-protocol` to v2.2.0
- [ ] Run existing tests to verify compatibility
- [ ] Migrate `SnapshotStore` → `AsyncSnapshotStore` (recommended)
- [ ] Add `ASAP-Version` header handling (automatic with ASAPClient)
- [ ] Evaluate per-agent identity for your use case (optional)
- [ ] Define capability grants if using multi-agent authorization (optional)
- [ ] Adopt `RecoverableError` / `FatalError` in custom error handling (recommended)
- [ ] Use `ASAPClient.batch()` for multi-request flows (optional)
- [ ] Enable `audit_store` in `create_app()` for tamper-evident logging (optional)
- [ ] Run `run_compliance_harness_v2(app)` to validate your server (recommended)

### Upgrading from v2.2.0 to v2.2.1

v2.2.1 adds an **optional** WebAuthn stack for real proof-of-possession on
high-risk registration paths. If you do nothing, behavior stays the same as
v2.2.0.

#### Optional extra

Install the PyPI extra so the `webauthn` library is available:

```bash
pip install 'asap-protocol[webauthn]'
# or
uv sync --extra webauthn
```

#### Default behavior (unchanged without configuration)

`create_app` still uses `default_webauthn_verifier()` from
`asap.auth.self_auth`. That returns `PlaceholderWebAuthnVerifier` when:

- the `webauthn` package is not installed, **or**
- `ASAP_WEBAUTHN_RP_ID` or `ASAP_WEBAUTHN_ORIGIN` is not set.

In that case, WebAuthn checks in the reference server **do not** perform
cryptographic verification (same as v2.2.0).

#### Production recommendation

For deployments that enforce self-authorization prevention with real passkeys:

1. Install `asap-protocol[webauthn]`.
2. Set `ASAP_WEBAUTHN_RP_ID` (no scheme; e.g. `app.example.com`) and
   `ASAP_WEBAUTHN_ORIGIN` (full origin; e.g. `https://app.example.com`).
3. Optionally pass a durable `WebAuthnCredentialStore` (see
   `asap.auth.webauthn`) via a custom `identity_webauthn_verifier` instead of
   the in-memory default.

See [Self-authorization prevention](security/self-authorization-prevention.md)
(**Real WebAuthn**) for the threat model and ceremony flow.

### Upgrading from v2.4.1 to v2.5.0

v2.5.0 is an **additive, backward-compatible** minor release. JSON-RPC envelopes,
OAuth2, capability grants, and existing manifests are unchanged. MCP servers
without `protect_server` behave exactly as in v2.4.1.

#### What changed

- **MCP Auth Bridge (opt-in)**: `asap.mcp.auth.protect_server` wraps a native
  stdio `MCPServer` with Agent JWT verification and capability enforcement on
  `tools/call`. Unprotected MCP usage remains valid (MCP-DOC-004).
- **Compliance**: `asap-compliance` adds an `mcp-auth-bridge` profile for stdio
  MCP auth, grants, constraints, and manifest alignment.
- **Deferred**: `initialize` session-token handshake, `hide_unauthorized_tools`
  (MAP-004), and `@asap-protocol/mcp-auth` HTTP/SSE middleware (v2.5.0.1).

#### Upgrade steps

1. **Bump Python dependency**:
   ```bash
   pip install --upgrade asap-protocol==2.5.0
   # or
   uv add asap-protocol==2.5.0
   ```
   TypeScript consumers: no npm bump required for v2.5.0 — `@asap-protocol/*`
   packages remain at **2.4.1** until the v2.5.0.1 middleware release.

2. **Optional — protect an MCP server**: See
   [MCP Auth Bridge adapter](adapters/mcp-auth-bridge.md) and
   `examples/mcp_auth_bridge/server.py`.

3. **Re-run Compliance Harness v2** if you ship MCP tools
   (`asap compliance-check --exit-on-fail`).

#### Backward compatibility

- **Wire protocol**: Unchanged from v2.4.1.
- **Breaking changes**: None. `protect_server` is opt-in.

---

### Upgrading from v2.5.0 to v2.5.1

v2.5.1 is a **code quality patch**. JSON-RPC envelopes, OAuth2, capability grants,
manifests, and the MCP Auth Bridge are unchanged. Most deployments upgrade with no
code changes; the one operator-relevant case is WebSocket in OAuth2-only
deployments (see below).

#### What changed

- **Behavior-preserving refactors**: transport (`server`/`client`/`websocket` split
  into packages), SQLite storage consolidated onto a shared `AsyncSqliteRepository`,
  `auth/` module reorg, and shared integration plumbing. Public import paths are
  preserved via re-export shims.
- **Six fixes**: atomic `revoke_cascade`; canonical `usage_events` DDL; unified
  Host-JWT verifier (revoked host → 403); **WebSocket now enforces OAuth2**; OpenAPI
  handler import/constructor cleanup; client-side `correlation_id` binding.
- **Deprecated import paths** (removed in v2.6.0): `asap.transport.websocket`,
  `asap.adapters.mcp`, `RemoteFatalRPCError`/`RemoteRecoverableRPCError`,
  `metering_storage_adapter`, `FRAME_ENCODING_BINARY`.

#### Operator action: OAuth2-only WebSocket deployments

If you run `OAuth2Middleware` **without** `manifest.auth` / `token_validator`
(OAuth2-only), the WebSocket endpoint `/asap/ws` was previously unauthenticated — it
skipped the Starlette middleware stack. It now requires a Bearer JWT in the
handshake `Authorization` header and rejects with close code **4401** when absent or
invalid. Connect your WS clients with a Bearer token. Deployments without an OAuth2
IdP are unchanged.

#### Upgrade steps

1. **Bump dependency**:
   ```bash
   pip install --upgrade asap-protocol==2.5.1
   # or
   uv add asap-protocol==2.5.1
   ```
   TypeScript consumers: no npm bump required — `@asap-protocol/*` stays at **2.4.1**.

2. **Migrate deprecated imports** at your convenience (removed in v2.6.0):
   ```python
   # Before
   from asap.transport.websocket import WebSocketTransport
   from asap.adapters.mcp import protect_server, MCPAuthConfig

   # After
   from asap.transport.ws import WebSocketTransport
   from asap.mcp.auth import protect_server, MCPAuthConfig
   ```
   `from asap.transport.client import ASAPClient` and
   `from asap.transport.server import create_app` are unchanged.

3. **OAuth2-only WS operators**: pass a Bearer JWT on the WS handshake (see above).

4. **Re-run Compliance Harness v2** after upgrading production agents
   (`asap compliance-check --exit-on-fail`).

#### Backward compatibility

- **Wire protocol**: Unchanged from v2.5.0.
- **Breaking changes**: None. Deprecated imports keep working via shims until v2.6.0.
- **Behavior change**: WebSocket OAuth2 enforcement (above) closes an authentication
  gap; only OAuth2-only WS deployments are affected.

---

### Upgrading from v2.5.1 to v2.5.2

**v2.5.2** is a security & correctness follow-up ([PRD](../product/prd/prd-v2.5.2-security-follow-up.md)).
It may include small CLI surface trims and tighter ingress validation (e.g. rejecting
unknown `config` / `metadata` keys) without changing the JSON-RPC envelope wire format.

#### CLI legacy import paths (#242)

The v2.5.1 S3 CLI split moved command groups into submodules but kept three
legacy names on `asap.cli` root for compatibility. Those root re-exports are
removed; use `asap.cli._compat` or canonical modules until **v2.6.0**
([#275](https://github.com/asap-protocol/asap-protocol/issues/275)):

```python
# Before (no longer works)
from asap.cli import DEFAULT_SCHEMAS_DIR, export_all_schemas, _repl_namespace

# After — shim (removed in v2.6.0)
from asap.cli._compat import DEFAULT_SCHEMAS_DIR, export_all_schemas, _repl_namespace

# After — canonical (preferred)
from asap.cli.schemas import DEFAULT_SCHEMAS_DIR
from asap.schemas import export_all_schemas
from asap.cli.repl import _repl_namespace
```

`asap = "asap.cli:main"` and `from asap.cli import app, main` are unchanged.

#### Opt-in operator API auth (#209)

`/usage`, `/sla`, and `/audit` remain **open by default** (local/operator ergonomics).
To require OAuth2 when exposing them beyond localhost:

```python
from asap.auth import OAuth2Config
from asap.transport.server import create_app

app = create_app(
    manifest,
    oauth2_config=OAuth2Config(jwks_uri="https://idp.example.com/jwks.json"),
    metering_storage=...,
    sla_storage=...,
    audit_store=...,
    require_operator_auth=True,
    # Bearer JWT must include scope asap:admin AND pass identity binding
    # (custom claim = manifest.id, or sub in ASAP_AUTH_SUBJECT_MAP).
)
```

Issue tokens from your IdP with **both** scope and identity binding — a common
first-deploy mistake is `asap:admin` without the custom claim (403 identity mismatch):

```python
# Example JWT claims for an operator / dashboard token
{
    "sub": "urn:asap:agent:operator",  # or auth0|... / human IdP subject
    "scope": "asap:admin",
    # Default claim key (override with ASAP_AUTH_CUSTOM_CLAIM):
    "https://github.com/adriannoes/asap-protocol/agent_id": manifest.id,
}
```

If the IdP cannot emit custom claims, map `sub` via `ASAP_AUTH_SUBJECT_MAP`
instead. See [Configuring Custom Claims](security/v1.1-security-model.md#configuring-custom-claims)
and [Allowlist fallback](security/v1.1-security-model.md#option-2-allowlist-fallback).

`require_operator_auth=True` without `oauth2_config` raises `ValueError` at
startup. Operator tokens must:

- carry scope ``asap:admin`` (``asap:execute`` alone yields 403); and
- satisfy identity binding — the JWT custom claim (`ASAP_AUTH_CUSTOM_CLAIM`,
  default `agent_id`) must equal the server `manifest.id`, or the subject must be
  listed in `ASAP_AUTH_SUBJECT_MAP`. Human/operator IdP tokens without this claim
  get **403 identity mismatch** (same rule already applied to `/asap`).

If you set `OAuth2Config.required_scope` for `/asap` tasks, it is **not** applied
to `/usage`, `/sla`, or `/audit`: those enforce only ``asap:admin`` via the route
dependency, so an admin-only token is accepted regardless of the global scope.

#### Ingress validation: `TaskRequestConfig` and `CommonMetadata` (#209)

`TaskRequest.config` and `Conversation.metadata` now reject unknown keys
(`extra="forbid"`). Custom extension data that previously rode in ad-hoc
`config` or `metadata` properties must move to `TaskRequest.input` or envelope
`extensions`.

```python
# Before (accepted arbitrary keys)
TaskRequest(
    conversation_id="conv_1",
    skill_id="research",
    input={"query": "AI"},
    config={"timeout_seconds": 60, "custom_flag": True},
)

# After — drop unknown config keys or relocate them
TaskRequest(
    conversation_id="conv_1",
    skill_id="research",
    input={"query": "AI", "custom_flag": True},
    config={"timeout_seconds": 60},
)
```

`TaskResponse.metrics` still allows extra fields (unchanged in this release).

#### Redis-backed JTI replay cache (#209)

Single-process deployments keep the default in-memory
:class:`~asap.auth.agent_jwt.JtiReplayCache`. Multi-worker or multi-instance
hosts should share replay state via
:class:`~asap.auth.jti_replay_cache.RedisJtiReplayCache` (requires
``pip install 'asap-protocol[redis]'``):

```python
from asap.auth.jti_replay_cache import RedisJtiReplayCache
from asap.transport.server import create_app

jti_cache = RedisJtiReplayCache.from_url("redis://localhost:6379/0")
app = create_app(manifest, identity_jti_cache=jti_cache, ...)
```

MCP Auth Bridge deployments can pass the same instance through
``MCPAuthConfig(jti_replay_cache=jti_cache)``.

#### Web app distributed rate limits (#209)

Self-hosted or multi-instance `apps/web` deployments can share dashboard and
proxy rate-limit counters across replicas by configuring Upstash Redis REST
credentials (or Vercel KV, which uses the same client):

```bash
# Upstash Redis
UPSTASH_REDIS_REST_URL=https://....upstash.io
UPSTASH_REDIS_REST_TOKEN=...

# Vercel KV (when linked to the project)
# KV_REST_API_URL=...
# KV_REST_API_TOKEN=...
```

When these variables are unset, behavior is unchanged from v2.5.1: per-instance
in-memory limits in `apps/web/src/lib/rate-limit.ts`. No code changes are
required for local development.

The Redis backend uses a **fixed window** aligned with the in-memory fallback.
If Redis is unreachable, the app falls back to per-instance in-memory limits
(weaker on multi-instance deploys); set both URL and token from the same
provider family (Upstash **or** Vercel KV) to avoid misconfiguration.

#### Other v2.5.2 correctness fixes

| Fix | Impact |
|-----|--------|
| **Streaming correlation (#247)** | SSE/WebSocket `TaskStream` chunks must set `correlation_id` to the **request envelope `id`**. Mismatched or missing binding raises client-side `ProtocolCorrelationError`. |
| **Agent status JTI (#249)** | `GET /asap/agent/status` rejects Host JWTs whose `jti` was already consumed by register/revoke/rotate-key (read-only replay check; polling may still reuse a fresh token). |
| **Registry signed envelopes (#224)** | Auto-registration accepts `{manifest, signature}` envelopes and unwraps before validation. |
| **Registry validation → 400 (#227)** | `ManifestValidationError` maps to HTTP **400** (was 500). |
| **SQLite write lock (#245)** | Shared `asyncio.Lock` on `AsyncSqliteRepository.execute()` for concurrent writers. |

Allowed `TaskRequest.config` fields (unknown keys rejected):
`timeout_seconds`, `priority`, `idempotency_key`, `streaming`, `persist_state`,
`model`, `temperature`.

Allowed `CommonMetadata` fields: `purpose`, `ttl_hours`, `source`, `timestamp`,
`tags`.

#### Backward compatibility

- **Opt-in**: operator auth, Redis JTI, web distributed rate limits — unchanged
  defaults when you do nothing.
- **Breaking for clients**: unknown keys on `TaskRequest.config` /
  `Conversation.metadata` now fail validation.
- **Wire protocol / manifest schema**: otherwise unchanged.
- **CLI**: root re-exports of three legacy names removed; use `_compat` or
  canonical modules (shim removed in v2.6.0).

#### Migration checklist

- [ ] Bump to `asap-protocol>=2.5.2`
- [ ] Scan clients for unknown `config` / `metadata` keys; move extensions to
      `TaskRequest.input` or envelope `extensions`
- [ ] If exposing `/usage`, `/sla`, or `/audit` beyond localhost: set
      `require_operator_auth=True` + `oauth2_config` and mint ``asap:admin``
      tokens with identity binding
- [ ] Multi-worker HTTP: `identity_jti_cache=RedisJtiReplayCache.from_url(...)`
      and `ASAP_RATE_LIMIT_BACKEND=redis://...`
- [ ] Multi-worker MCP Auth Bridge: same cache via `MCPAuthConfig.jti_replay_cache`
- [ ] Multi-instance `apps/web`: set Upstash or Vercel KV REST URL + token
- [ ] Custom stream handlers: ensure `TaskStream.correlation_id == request.id`
- [ ] Registry integrators: expect HTTP 400 on manifest validation failures;
      signed envelope JSON is accepted
- [ ] Re-run compliance harness / CI gate after upgrade

#### Troubleshooting (v2.5.2)

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Pydantic `ValidationError` on `config` / `metadata` | Unknown keys (`extra="forbid"`) | Use allowed fields only; nest custom data under `input` / `extensions` |
| Operator API **401** | Missing/invalid Bearer JWT | Configure IdP JWKS; send `Authorization: Bearer ...` |
| Operator API **403** with ``asap:admin`` | Identity binding failed, or scope missing | Set custom claim = `manifest.id` or map `sub` via `ASAP_AUTH_SUBJECT_MAP` |
| `ProtocolCorrelationError` on stream | Chunk `correlation_id` ≠ request envelope `id` | Echo `request_envelope.id` on every streamed chunk |
| Host JWT rejected on `/asap/agent/status` | `jti` already recorded by a mutating route | Mint a new Host JWT for polling after register/revoke/rotate |
| Redis JTI / rate-limit errors on HTTP | Redis unreachable | Redis errors **propagate** for JTI and transport rate limits — monitor Redis health |

See also [Troubleshooting Guide](troubleshooting.md) and
[Security — Operator REST APIs](security.md#operator-rest-apis-v252).

---

### Upgrading from v2.5.3 to v2.5.4

**v2.5.4 (Distribution Loop)** — **shipped** ([tag `v2.5.4`](https://github.com/asap-protocol/asap-protocol/releases/tag/v2.5.4),
[#294](https://github.com/asap-protocol/asap-protocol/pull/294)) —
turns adoption surfaces into a repeatable loop: homepage routing, thin starters,
Build for agents guide, and maintainer telemetry ops. There are **no breaking
changes** for envelope, JWT, or capability grant semantics relative to v2.5.3.
Scope: [prd-v2.5.4-distribution-loop.md](../product/prd/prd-v2.5.4-distribution-loop.md).

#### What lands in v2.5.4

- **Thin starters** — `examples/starters/openapi-provider/`,
  `typescript-consumer/`, and `mcp-auth-bridge/` (wrappers over existing examples).
- **Build for agents** — [guide](guides/build-for-agents.md) as the agent-first
  onboarding path into those starters.
- **Homepage** — agent-first hero / CTA routing to docs, starters, and examples
  (marketplace browse/register remain secondary).
- **Telemetry ops** — npm collectors cover ≥3 scoped packages; PyPI aggregate
  covers `asap-protocol` + `asap-compliance`; maintainer runbook; **no** public
  live metrics UI.

#### Upgrade steps

1. Bump with `pip install 'asap-protocol==2.5.4'` (or `uv add asap-protocol`).
2. No code changes required for existing v2.5.3 agents unless you adopt the new
   starters or guide.
3. TypeScript `@asap-protocol/*` packages remain at **2.4.1**;
   `@asap-protocol/mcp-auth` HTTP/SSE middleware remains deferred.
4. Optional: start from [Build for agents](guides/build-for-agents.md) and run
   one starter smoke from `examples/starters/README.md`.
5. If you use the optional `[mcp]` extra, bump to `mcp>=1.28.1` (security floor
   for CVE-2026-52869 / CVE-2026-52870 / CVE-2026-59950).

#### Backward compatibility

- **Wire protocol**: Unchanged from v2.5.3.
- **Breaking changes**: None for Distribution Loop scope (docs / starters /
  homepage / maintainer telemetry).

### Unreleased (after v2.5.4) — custom AgentStore

Custom ``AgentStore`` authors on git ``main`` / the next release must:

1. Implement ``touch_if_current``. ``verify_agent_jwt`` no longer full-row
   ``save``s a verify-time snapshot. Missing the method is ``AttributeError`` on
   the next Agent JWT verify. See [Custom Agent Store](security.md#custom-agent-store).
2. Make ``save`` refuse revoked→non-revoked (raise
   ``RevokedAgentOverwriteError``). A get-then-overwrite that awaits I/O can
   resurrect a concurrent ``revoke``.

---

### Upgrading from v2.5.2 to v2.5.3

**v2.5.3 (Adapter Lab II)** — **shipped** ([tag `v2.5.3`](https://github.com/asap-protocol/asap-protocol/releases/tag/v2.5.3),
[#291](https://github.com/asap-protocol/asap-protocol/pull/291)) — is primarily **documentation
and examples**, plus small DX / correctness fixes. There are **no breaking changes**
for envelope, JWT, or capability grant semantics relative to v2.5.2.

#### What lands in v2.5.3

- **Workflow connectors** — OpenAPI → ASAP skills for n8n / Activepieces-style
  workflow HTTP APIs ([guide](integrations/workflow-connectors.md),
  `examples/workflow_asap_connector/`).
- **Automation connector security** — production baseline for OpenAPI-backed
  connectors ([guide](guides/automation-connector-security.md)).
- **Microsoft Agent Framework** — research / experimental interop guide only
  ([guide](integrations/microsoft-agent-framework.md)); no .NET SDK / NuGet.
- **NeMo Agent Toolkit** — experimental Path A demo
  ([guide](integrations/nemo-agent-toolkit.md),
  `examples/nemo_agent_toolkit_asap/`); Path C third-party plugin remains deferred.
- **MkDocs / web** — Adapters + Lab II nav; marketplace WhatsNew ribbon for Lab II.
- **Fixes**: JSON-safe invalid-envelope `-32602`; MCP Auth Bridge example client
  self-captures child JWT; `health_check()` defaults to the client base URL;
  `setuptools>=83` override for **PYSEC-2026-3447**.

#### Upgrade steps

1. Bump with `pip install 'asap-protocol==2.5.3'` (or `uv add asap-protocol`).
2. No code changes required for existing v2.5.2 agents unless you adopt the new
   examples or guides.
3. TypeScript `@asap-protocol/*` packages remain at **2.4.1**;
   `@asap-protocol/mcp-auth` HTTP/SSE middleware remains deferred.
4. Optional: re-run `examples/mcp_auth_bridge/client.py` without pasting a JWT —
   the client now captures the demo token from its own child stderr.

#### Backward compatibility

- **Wire protocol**: Unchanged from v2.5.2.
- **Breaking changes**: None for Lab II scope (docs / examples / DX).

---

### Upgrading from v2.4.0 to v2.4.1

v2.4.1 is a **security-hardening patch**. JSON-RPC envelopes, capability grants,
and manifest schemas are unchanged from v2.4.0. Most deployments upgrade without
code changes.

#### What changed

- **OAuth2 JWT validation (opt-in)**: When `ASAP_AUTH_ISSUER` and/or
  `ASAP_AUTH_AUDIENCE` are set (or `OAuth2Config.expected_issuer` /
  `expected_audience`), the OAuth2 middleware validates JWT `iss` and `aud` via
  `validate_jwt()` in `asap.auth.jwks`. Tokens with a mismatched issuer or
  audience are rejected.
- **Identity binding (fail-closed)**: When `manifest_id` is configured on
  `OAuth2Config`, requests whose JWT subject does not match the custom claim
  (`ASAP_AUTH_CUSTOM_CLAIM`) or `ASAP_AUTH_SUBJECT_MAP` allowlist now receive
  **403** instead of warn-and-pass.
- **Web app (`apps/web`)**: Hardened against open redirects on E2E fixture login
  routes (`resolveRedirectUrl`), SSRF on `/api/health-check` (127.0.0.0/8, DNS
  resolve4/6), and strict Zod validation on public API query params (unknown keys
  return 400). Default marketplace deployments pick up these fixes on rebuild —
  **no operator configuration required**.
- **Dependencies**: Raised `fastapi` floor to `>=0.136.1` (pulls
  `starlette>=1.0.1`, resolves **PYSEC-2026-161**); bumped transitive pins for
  `langchain-core`, `langsmith`, `python-multipart`, `urllib3`, `pip`, and
  `smolagents`. See [SECURITY.md](../SECURITY.md).

#### Upgrade steps

1. **Bump dependencies**:
   ```bash
   pip install --upgrade asap-protocol==2.4.1
   # or
   uv add asap-protocol==2.4.1
   ```
   TypeScript consumers: `npm install @asap-protocol/client@2.4.1`.

2. **OAuth2 operators**: If you already set `ASAP_AUTH_ISSUER` /
   `ASAP_AUTH_AUDIENCE`, ensure your IdP issues tokens with matching `iss` and
   `aud`. If unset, behavior is unchanged from v2.4.0.

3. **Identity binding**: If `manifest_id` is set, verify
   `ASAP_AUTH_CUSTOM_CLAIM` or `ASAP_AUTH_SUBJECT_MAP` covers every legitimate
   caller — otherwise those callers will now receive 403.

4. **Web app**: Rebuild/redeploy `apps/web` if you self-host the marketplace
   (fixes ship in the image; no new env vars).

5. **Re-run Compliance Harness v2** after upgrading production agents
   (`asap compliance-check --exit-on-fail`).

#### Backward compatibility

- **Wire protocol**: Unchanged from v2.4.0.
- **Breaking changes**: None when `ASAP_AUTH_ISSUER`, `ASAP_AUTH_AUDIENCE`, and
  `manifest_id` identity binding were already configured correctly. Fail-closed
  identity binding is a behavior change only for misconfigured deployments that
  previously passed with warnings.

---

### Upgrading from v2.3.x to v2.4.0

v2.4.0 is an **additive, backward-compatible** minor release. JSON-RPC envelopes,
OAuth2, capability grants, and existing manifests **without** `capabilities.hardware`
or `capabilities.inference` remain valid.

#### What is new

- **Optional manifest fields**: `capabilities.hardware` (`class`, `model`, `io`)
  and `capabilities.inference` (`modes`, `local_models`) with closed enums.
  See [Transport — hardware and inference](transport.md#hardware-and-inference-capabilities-v24).
- **Lite Registry mirror**: `hardware_class`, `inference_modes`, `hardware_io`
  on `RegistryEntry`, derived at auto-registration or IssueOps when a manifest
  URL is loaded — do not duplicate these in the registration JSON body.
- **Discovery helpers** (Python): `find_by_hardware_class`, `find_by_inference_mode`,
  `find_by_io` in `asap.discovery.registry`.
- **Marketplace (web)**: Browse filters and agent detail blocks for edge-AI fields.
- **TypeScript SDK**: `@asap-protocol/client@2.4.0` — optional hardware fields on
  `RegistryEntry` in `discovery.ts`.
- **ShellClaw onboarding**: [ShellClaw registry guide](guides/shellclaw-registry.md)
  for static manifest URLs and `online_check: false`.

#### Upgrade steps

1. **No action required** if you do not advertise edge hardware or inference modes.
2. **Python**: `pip install --upgrade asap-protocol==2.4.0` (or `uv add asap-protocol`).
3. **TypeScript**: `npm install @asap-protocol/client@2.4.0` when using registry
   discovery types with hardware fields.
4. **Registrants (optional)**: Add structured `hardware` / `inference` to your manifest;
   keep existing `tags` (e.g. `jetson`, `cuda`) as supplements until you migrate.
5. **Re-run Compliance Harness v2** after changing manifest or registry-facing fields.

#### Backward compatibility

- **Wire protocol**: Unchanged from v2.3.x.
- **Breaking changes**: None.
- **Community feedback**: Enum extensions tracked on [#176](https://github.com/asap-protocol/asap-protocol/issues/176).

---

### Upgrading from v2.3.0 to v2.3.1

v2.3.1 is an **additive, TypeScript-only** patch. The Python `asap-protocol`
package, JSON-RPC wire format, and envelope schemas are **unchanged**. Existing
v2.3.0 Python and TypeScript deployments continue to work without code changes.

#### What is new

- **`@asap-protocol/mastra@2.3.1`**: Mastra `createTool` integration. See
  [Mastra integration](integrations/mastra.md).
- **`@asap-protocol/openai-agents@2.3.1`**: OpenAI Agents SDK `tool()` integration.
  See [OpenAI Agents integration](integrations/openai-agents.md).
- **`@asap-protocol/client@2.3.1`**: Optional patch bump with additive adapter
  exports (`adapters/shared`, output-schema helpers). Stay on `@2.3.0` if you do
  not use the new packages.

#### Upgrade steps

1. **No action required** for Python-only or existing `@asap-protocol/client@2.3.0`
   consumers.
2. **To adopt a new adapter**, install the package and its peers:
   ```bash
   npm install @asap-protocol/mastra @asap-protocol/client @mastra/core zod
   # or
   npm install @asap-protocol/openai-agents @asap-protocol/client @openai/agents zod
   ```
3. **Re-run Compliance Harness v2** if you add a new adapter-backed agent to
   production (`asap compliance-check --exit-on-fail`).

#### Backward compatibility

- **Wire protocol**: Unchanged from v2.3.0.
- **Breaking changes**: None.

---

### Upgrading from v2.2.x to v2.3.0

v2.3.0 is an **Adoption Multiplier** release. JSON-RPC envelopes, batching, and
`ASAP-Version` negotiation are unchanged; new surfaces are **opt-in** behind
explicit `create_app` flags and optional extras.

#### What is new

- **OpenAPI Adapter**: Install `asap-protocol[openapi]`, then use
  `asap.create_from_openapi` (or `from asap import create_from_openapi`) to map
  an OpenAPI 3.x document to ASAP capabilities. See
  [OpenAPI adapter](adapters/openapi.md).
- **TypeScript client**: Publish/consume via npm package
  `@asap-protocol/client@2.3.0` (see repository `packages/typescript/client/`).
- **Auto-Registration**: When `registry_auto_registration` is configured on
  `create_app`, clients can call `POST /registry/agents` with a registration
  token; Compliance Harness v2 gates merges. See
  [Auto-registration](registry/auto-registration.md).
- **Capability escalation**: Agents may call
  `POST /asap/agent/request-capability` to request additional grants; Python
  `ASAPClient.request_capability` and the TS client expose the same flow. See
  [Capability escalation](capabilities/escalation.md).
- **ASAP challenge**: Resource servers may attach
  `WWW-Authenticate: ASAP discovery="<manifest-url>"` on selected 401/403
  responses; enable via `create_app` challenge middleware when integrating
  unknown callers. See [ASAP challenge](transport/asap-challenge.md).

#### Upgrade steps

1. **Bump dependencies**:
   ```bash
   pip install --upgrade 'asap-protocol[openapi]==2.3.0'
   # or
   uv add 'asap-protocol[openapi]==2.3.0'
   ```
   TypeScript consumers: `npm install @asap-protocol/client@2.3.0`.

2. **Adopt features incrementally**: Add OpenAPI-derived agents, TS clients,
   auto-registration, escalation, or challenge middleware only where your
   deployment needs them — defaults preserve v2.2.x routing.

3. **Re-run Compliance Harness v2** against every production agent base URL
   after enabling new routes (`asap compliance-check --exit-on-fail`).

#### Backward compatibility

- **Wire protocol**: Compatible with v2.2.x clients and servers when new routes
  are not enabled.
- **Security posture**: Treat auto-registration tokens, escalation approvals,
  and discovery URLs as security-sensitive configuration (rate limits, HTTPS,
  manifest integrity). See PRD
  [`prd-v2.3-scale.md`](https://github.com/asap-protocol/asap-protocol/blob/main/product/prd/prd-v2.3-scale.md).

---

## Troubleshooting

### Common Issues

| Issue | Cause | Solution |
|-------|-------|----------|
| `Invalid agent URN` | URN format incorrect | Use `urn:asap:agent:{name}` |
| `Missing correlation_id` | Response without correlation | Add `correlation_id` to responses |
| `Method not found` | Wrong JSON-RPC method | Use `asap.send` |
| `Invalid envelope` | Missing required fields | Check `sender`, `recipient`, `payload_type` |

### Validation

Use CLI to validate messages:

```bash
# Export schemas for reference
asap export-schemas --output-dir ./schemas

# List available schemas
asap list-schemas

# Show specific schema
asap show-schema Envelope
```

---

## Related Documentation

- [Transport](transport.md) - HTTP/JSON-RPC details
- [Security](security.md) - Authentication setup
- [State Management](state-management.md) - Snapshots and lifecycle
- [API Reference](api-reference.md) - Complete API docs
