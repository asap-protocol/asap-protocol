# MCP Auth Bridge adapter (Python)

The `asap.mcp.auth` package adds **opt-in** Agent JWT and capability enforcement to a native **stdio MCP** server (`MCPServer`). Call `protect_server` to wrap an existing server without forking the JSON-RPC protocol loop (Mode A).

> **Import path (v2.5.1+):** Prefer `from asap.mcp.auth import ...`. The legacy
> `asap.adapters.mcp` package remains a deprecation shim until **v2.6.0**.

**Default behavior:** Unprotected `MCPServer` usage remains valid. Protection is explicit (MCP-DOC-004).

**Out of scope (v2.5.0):** An `initialize` session-token handshake is **deferred** per the [design lock](../../engineering/tasks/v2.5.0/design-lock-mcp-auth-bridge.md) — not shipped in this release. Clients must pass the Agent JWT on each protected `tools/call` via `_meta.asap_agent_jwt`.

**TypeScript package status:** This guide covers the **Python stdio** bridge shipped in v2.5.0. HTTP/SSE middleware for `@asap-protocol/mcp-auth` (MCP-TS-001..003) is not part of the v2.5.x npm line; see the [TypeScript spike](../../engineering/tasks/v2.5.0/typescript-mcp-auth-spike.md) and [backlog](../../engineering/tasks/v2.5.0/backlog-mcp-auth-typescript.md). Git tag **`v2.5.0.1`** published **`asap-compliance` 1.3.0** only. Existing `@asap-protocol/*` npm packages remain at **2.4.1**.

For MCP-over-ASAP envelopes (Mode B), see [MCP integration](../mcp-integration.md).

## Architecture

```text
stdio MCP client
        │
        ▼
  tools/call params
        │
        ▼
  CallToolRequestParams (incl. _meta)
        │
        ▼
  resolve_jwt_extractor(config)
        │  reads params.meta["asap_agent_jwt"]
        │  (optional dev-only ASAP_AGENT_JWT when allow_env_jwt_fallback=True)
        ▼
  missing token? ──► CallToolResult isError + asap:auth_required
        │              (skipped for public_tools)
        ▼
  verify_agent_jwt(host_store, agent_store, jti_cache, audience)
        │
        ▼
  invalid? ──► asap:invalid_token
        │
        ▼
  resolve_capability(tool_name, config)
        │  tool_capability_map → register-time metadata → identity (tool name)
        ▼
  capability_registry.check_grant(agent_id, capability, arguments)
        │
        ▼
  denied / constraint fail? ──► asap:capability_denied / asap:constraint_violation
        │
        ▼
  MCPServer._handle_tools_call  ──► schema validate + tool handler
```

- **Single interception point:** `ProtectedMCPServer` overrides `_handle_tools_call` only; `serve_stdio`, `_dispatch_request`, and notification handling are unchanged (MCP-AUTH-006).
- **No mutation:** `protect_server` returns a new server instance; the input `MCPServer` is not modified.
- **Shared grant store:** Use the same `CapabilityRegistry` as your A2A HTTP surface — there is no parallel MCP-specific grant database in v2.5.0.
- **`tools/list`:** Unchanged by default. Filtering unauthorized tools (`hide_unauthorized_tools`) is deferred (MCP-MAP-004) because stdio `tools/list` has no standard JWT carriage today.

## Quick usage

Wire identity stores, register capabilities and grants, register MCP tools, then wrap with `protect_server`:

```python
import asyncio
from datetime import datetime, timezone

from asap.mcp.auth import MCPAuthConfig, protect_server
from asap.auth.capabilities import (
    CapabilityDefinition,
    CapabilityGrant,
    CapabilityRegistry,
    GrantStatus,
)
from asap.auth.identity import InMemoryAgentStore, InMemoryHostStore
from asap.mcp.server import MCPServer


async def echo(message: str) -> dict[str, str]:
    return {"message": message}


async def secure_action(action: str) -> dict[str, str]:
    return {"status": "ok", "action": action}


async def main() -> None:
    agent_store = InMemoryAgentStore()
    host_store = InMemoryHostStore(agent_store=agent_store)
    registry = CapabilityRegistry()

    registry.register(
        CapabilityDefinition(
            name="secure_action",
            description="Protected MCP tool",
            input_schema={"type": "object", "properties": {"action": {"type": "string"}}},
        )
    )
    now = datetime.now(timezone.utc)
    registry.grant(
        CapabilityGrant(
            grant_id="demo-grant",
            agent_id="urn:asap:agent:demo",
            capability="secure_action",
            status=GrantStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
    )

    server = MCPServer()
    server.register_tool(
        "echo",
        echo,
        {"type": "object", "properties": {"message": {"type": "string"}}},
        description="Public echo tool",
    )
    server.register_tool(
        "secure_action",
        secure_action,
        {"type": "object", "properties": {"action": {"type": "string"}}},
        description="Requires Agent JWT + grant",
        capability="secure_action",
    )

    config = MCPAuthConfig(
        host_store=host_store,
        agent_store=agent_store,
        capability_registry=registry,
        public_tools=frozenset({"echo"}),
    )
    protected = protect_server(server, config)
    await protected.run_stdio()


if __name__ == "__main__":
    asyncio.run(main())
```

### Multi-worker JTI replay (v2.5.2)

Share JWT `jti` state across processes with the same Redis-backed cache used by
HTTP `create_app`:

```python
from asap.auth.jti_replay_cache import RedisJtiReplayCache
from asap.mcp.auth import MCPAuthConfig, protect_server

jti_cache = RedisJtiReplayCache.from_url("redis://localhost:6379/0")
config = MCPAuthConfig(
    host_store=host_store,
    agent_store=agent_store,
    capability_registry=registry,
    jti_replay_cache=jti_cache,
)
```

Requires `pip install 'asap-protocol[redis]'`. Redis connection errors propagate
(no fail-soft). See [Security — Multi-instance JWT replay](../security.md#multi-instance-jwt-replay-v252).

### Passing the Agent JWT

Clients attach the token on each protected `tools/call` in MCP `_meta`:

```json
{
  "name": "secure_action",
  "arguments": {"action": "deploy"},
  "_meta": {"asap_agent_jwt": "<Agent-JWT>"}
}
```

The JWT must include the resolved capability in its `capabilities` claim and the agent must hold an active grant in `CapabilityRegistry`.

### Dev-only environment fallback

For single-agent local testing, set `allow_env_jwt_fallback=True` on `MCPAuthConfig` and export `ASAP_AGENT_JWT`. **Do not enable in production** — a process-wide token bypasses per-call auth intent.

## Configuration reference

### `MCPAuthConfig`

| Field | Type | Default | Description |
|:------|:-----|:--------|:------------|
| `host_store` | `HostStore` | *(required)* | Host identity store for `verify_agent_jwt`. |
| `agent_store` | `AgentStore` | *(required)* | Agent session store for `verify_agent_jwt`. `save` MUST atomically refuse replacing a `revoked` row with a non-revoked snapshot (`RevokedAgentOverwriteError`; see [Custom AgentStore](../security.md#custom-agentstore-revocation-permanence)). |
| `capability_registry` | `CapabilityRegistry` | *(required)* | Shared capability definitions and grants; used by `check_grant`. |
| `tool_capability_map` | `dict[str, str]` | `{}` | Runtime override: MCP tool name → ASAP capability name (MCP-MAP-001). Checked first. |
| `public_tools` | `frozenset[str]` | `frozenset()` | Tool names that skip JWT verification entirely. |
| `enforce_grants` | `bool` | `True` | When `False`, JWT is verified but grant/constraint checks are skipped. |
| `hide_unauthorized_tools` | `bool` | `False` | **Deferred (MAY).** Does not filter `tools/list` in v2.5.0. |
| `validate_tools_at_startup` | `bool` | `False` | When `True`, `protect_server` fails fast if any registered tool resolves to an unknown capability (MCP-MAP-003). |
| `jwt_extractor` | `Callable[[CallToolRequestParams], str \| None] \| None` | `None` | Custom JWT extractor; when `None`, middleware uses `resolve_jwt_extractor`. |
| `allow_env_jwt_fallback` | `bool` | `False` | When `True`, read `ASAP_AGENT_JWT` if `_meta.asap_agent_jwt` is absent. **Dev only.** |
| `jti_replay_cache` | `JtiReplayCacheProtocol \| None` | `None` | Optional replay protection for JWT `jti` claims. Multi-worker: inject `RedisJtiReplayCache` (same instance as `create_app(identity_jti_cache=...)`). Redis errors propagate. |
| `expected_audience` | `str \| list[str] \| None` | `None` | Expected JWT `aud` passed to `verify_agent_jwt`. |
| `manifest_url` | `str \| None` | `None` | Optional HTTPS URL to the agent manifest for discovery alignment (see [Discovery](#discovery)). |

### Functions

| Function | Module | Description |
|:---------|:-------|:------------|
| `protect_server(server, config)` | `asap.mcp.auth` | Return a `ProtectedMCPServer` that enforces auth on `tools/call`. Input server is not mutated. |
| `resolve_jwt_extractor(config)` | `asap.mcp.auth.config` | Return the effective JWT extractor (custom `config.jwt_extractor` or `default_jwt_extractor` with `allow_env_jwt_fallback`). **Middleware must use this** — do not call a `None` extractor. |
| `resolve_capability(tool_name, config, *, server=None)` | `asap.mcp.auth.capability_map` | Resolve MCP tool name → ASAP capability. Order: `tool_capability_map` → register-time metadata on `server` → identity (`tool_name`). |
| `default_jwt_extractor(params, *, allow_env_fallback=False)` | `asap.mcp.auth.jwt_extractor` | Read `params.meta["asap_agent_jwt"]`; optional `ASAP_AGENT_JWT` env when `allow_env_fallback=True`. |

### Register-time capability metadata

Pass `capability=` when registering tools on `MCPServer` **before** `protect_server`, or on `ProtectedMCPServer` after wrapping:

```python
server.register_tool(
    "search",
    search_handler,
    input_schema,
    capability="web_search",
)
protected = protect_server(server, config)
```

Resolution still honors `tool_capability_map` overrides first.

### Public exports

Package root (`asap.mcp.auth`) exposes the primary integration surface:

```python
from asap.mcp.auth import MCPAuthConfig, ProtectedMCPServer, protect_server, resolve_jwt_extractor
```

Advanced helpers and error constants live in submodules:

```python
from asap.mcp.auth.config import resolve_jwt_extractor
from asap.mcp.auth.capability_map import resolve_capability
from asap.mcp.auth.errors import (
    AUTH_REQUIRED,
    CAPABILITY_DENIED,
    CONSTRAINT_VIOLATION,
    INVALID_TOKEN,
)
```

## Error codes

Protected `tools/call` failures return a `CallToolResult` with `isError: true`. The text content uses ASAP-namespaced codes from `asap.mcp.auth.errors`:

| Code | When | Typical detail |
|:-----|:-----|:---------------|
| `asap:auth_required` | No JWT in `_meta.asap_agent_jwt` (and no dev env fallback) on a non-`public_tools` call | — |
| `asap:invalid_token` | JWT signature, expiry, audience, replay, or identity validation failed | Verifier error message appended after `:` |
| `asap:capability_denied` | JWT `capabilities` claim missing the resolved capability, or no active grant | e.g. `JWT capabilities claim does not include 'secure_action'` |
| `asap:constraint_violation` | Grant exists but tool arguments fail constraint validation | Semicolon-joined violation messages |

Unknown tool names and schema validation errors remain the inner `MCPServer` behavior (not rewritten to ASAP auth codes).

Example error payload shape:

```json
{
  "content": [{"type": "text", "text": "asap:auth_required"}],
  "isError": true
}
```

## Security notes

- **Never log JWTs.** Middleware logs `agent_id` and `tool_name` on successful authorization only — not token material.
- **`allow_env_jwt_fallback` is dev-only.** Defaults to `False` so production cannot inherit a process-wide `ASAP_AGENT_JWT`.
- **Opt-in protection (MCP-DOC-004).** Existing unprotected MCP servers remain valid until operators call `protect_server`.
- **Use `resolve_jwt_extractor`.** Custom extractors must not bypass verification; env fallback is gated by config.
- **`public_tools` skip auth entirely.** A present `_meta.asap_agent_jwt` on a public tool is not verified — keep the public list minimal.
- **Shared registry.** Register `CapabilityDefinition` rows and issue `CapabilityGrant` records before exposing protected tools; JWT `capabilities` and registry grants must align.
- **Deferred `initialize` token (MCP-DOC-003).** Session tokens negotiated at MCP `initialize` are not implemented in v2.5.0; plan per-call `_meta` carriage or wait for a future release.

## Discovery

Operators may set `MCPAuthConfig.manifest_url` to the HTTPS URL where clients and registries fetch the agent's signed manifest. This does not auto-publish the manifest — it documents the canonical discovery endpoint for tooling that aligns MCP tool names with ASAP capability ids.

### Aligning `skills[].id` with MCP tools

Grant checks use **ASAP capability names**, not raw MCP tool names unless they are identical. Keep three surfaces consistent:

1. **MCP tool name** — `tools/call` `name` and `tools/list` entries.
2. **Resolved capability** — output of `resolve_capability` (`tool_capability_map`, register-time `capability=`, or identity default).
3. **Manifest `skills[].id`** — advertised to A2A clients and registry flows.

Example manifest excerpt for a protected MCP bridge (`echo` public, `secure_action` mapped 1:1):

```json
{
  "id": "urn:asap:agent:mcp-auth-bridge-demo",
  "name": "MCP Auth Bridge Demo",
  "version": "1.0.0",
  "description": "Native stdio MCP with ASAP capability grants",
  "capabilities": {
    "asap_version": "2.5",
    "skills": [
      {
        "id": "echo",
        "description": "Public echo tool (no JWT required)",
        "input_schema": {
          "type": "object",
          "properties": {"message": {"type": "string"}},
          "required": ["message"]
        },
        "output_schema": {"type": "object"}
      },
      {
        "id": "secure_action",
        "description": "Protected tool; requires Agent JWT + grant",
        "input_schema": {
          "type": "object",
          "properties": {"action": {"type": "string"}},
          "required": ["action"]
        },
        "output_schema": {"type": "object"}
      }
    ],
    "state_persistence": false,
    "streaming": false,
    "mcp_tools": ["echo", "secure_action"]
  },
  "endpoints": {
    "asap": "https://my-host.example/asap",
    "events": null
  }
}
```

When tool names differ from capability ids, reflect the mapping in config and manifest:

| MCP tool (`tools/call` name) | `tool_capability_map` | Manifest `skills[].id` |
|:-----------------------------|:----------------------|:-----------------------|
| `search` | `"search": "web_search"` | `web_search` |
| `secure_action` | *(identity)* | `secure_action` |

Set `manifest_url` to the published document, for example `https://my-host.example/.well-known/asap/manifest.json`.

### Registry cross-links

- [Lite Registry auto-registration](../registry/auto-registration.md) — `POST /registry/agents` with `manifest_url`; compliance harness validates the signed manifest before listing.
- [Transport — manifest discovery](../transport.md#get-well-knownasapmanifestjson---discovery-endpoint) — canonical `/.well-known/asap/manifest.json` shape and `Skill` fields.
- [Registry verification review](../guides/registry-verification-review.md) — separate path for the **Verified** badge (not auto-merge).

Listing `capabilities.mcp_tools` in the manifest helps clients discover which skills are exposed via native MCP versus pure A2A `task.request` handlers.

## Runnable example

The repo includes `examples/mcp_auth_bridge/` with a protected stdio server (`echo` public, `secure_action` protected), grant seeding, and JWT minting instructions.

```bash
uv run python examples/mcp_auth_bridge/server.py --help
```

## Common pitfalls

### Token carriage vs `initialize`

Do not assume an MCP `initialize` handshake will deliver session tokens — that pattern is **deferred**. Clients must send `_meta.asap_agent_jwt` on each protected `tools/call`.

### Capability name drift

If `tool_capability_map` renames a tool to a different capability, update manifest `skills[].id`, JWT `capabilities` claims, and `CapabilityRegistry` grants together. Enable `validate_tools_at_startup=True` during development to catch unregistered capabilities early.

### Public tool scope

Any tool in `public_tools` is callable without authentication. Do not add sensitive tools to this set.

### Mode A vs Mode B

This adapter protects **native stdio `MCPServer`** (Mode A). MCP invoked inside ASAP envelopes (`mcp.tool_call` / Mode B) follows the HTTP server's existing auth — see [MCP integration](../mcp-integration.md).

## Related documentation

- [MCP Auth Bridge starter](../../examples/starters/mcp-auth-bridge/) — thin smoke wrapper (`examples/starters/mcp-auth-bridge/`)
- [MCP integration](../mcp-integration.md) — Mode A vs Mode B, migration notes
- [NeMo Agent Toolkit](../integrations/nemo-agent-toolkit.md) — experimental Path A demo using `protect_server`
- [Automation connector security](../guides/automation-connector-security.md) — secrets, TLS, grants; Mode A vs Mode B for connectors
- [Security](../security.md) — Agent JWT, Host/Agent identity
- [Self-authorization prevention](../security/self-authorization-prevention.md) — grants and approval flows
- [Transport](../transport.md) — manifest discovery, `create_app`
- [Design lock: MCP Auth Bridge](../../engineering/tasks/v2.5.0/design-lock-mcp-auth-bridge.md) — hook strategy, deferred features
- [PRD v2.5.0 MCP Auth Bridge](https://github.com/asap-protocol/asap-protocol/blob/main/product/prd/prd-v2.5.0-mcp-auth-bridge.md) — MCP-AUTH-* / MCP-DISC-* requirements
