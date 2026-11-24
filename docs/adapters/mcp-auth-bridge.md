# MCP Auth Bridge

The MCP Auth Bridge lets an MCP server run a tool only for a caller that proves who it is and holds a grant for that tool. The caller is usually an ASAP agent: a program with its own Ed25519 key, run by a *host* (the app or service that owns the agent). A *capability* is a named permission, such as `secure_action`. A *grant* gives one caller a capability, with optional *constraints* on the tool arguments.

There are two implementations. They use the same four error codes.

| Your MCP server | Use | Read |
|-----------------|-----|------|
| Python, stdio transport (`asap.mcp.MCPServer`) | `asap.mcp.auth` in the `asap-protocol` Python package | The sections from [Architecture](#architecture) to [Common pitfalls](#common-pitfalls) |
| TypeScript or JavaScript on Node.js, Streamable HTTP, `@modelcontextprotocol/server` 2.x | The npm package `@asap-protocol/mcp-auth` | [HTTP MCP servers in TypeScript](#http-mcp-servers-in-typescript) |

The `asap.mcp.auth` package adds **opt-in** Agent JWT and capability enforcement to a native **stdio MCP** server (`MCPServer`). Call `protect_server` to wrap an existing server without forking the JSON-RPC protocol loop (Mode A).

> **Import path:** `from asap.mcp.auth import ...`. v2.6.0 removes the old
> import path; see the [migration guide](../migration.md#upgrading-from-v25x-to-v260).

**Default behavior:** Unprotected `MCPServer` usage remains valid. Protection is explicit (MCP-DOC-004).

**Out of scope (v2.5.0):** An `initialize` session-token handshake is **deferred** per the [design lock](../../engineering/tasks/v2.5.0/design-lock-mcp-auth-bridge.md) — not shipped in this release. Clients must pass the Agent JWT on each protected `tools/call` via `_meta.asap_agent_jwt`.

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
| `agent_store` | `AgentStore` | *(required)* | Agent session store for `verify_agent_jwt`. |
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

## HTTP MCP servers in TypeScript

`@asap-protocol/mcp-auth` protects an MCP server that serves Streamable HTTP with `@modelcontextprotocol/server` 2.x. It checks a bearer token on each HTTP request. Then, before a protected tool runs, it checks that the caller holds a grant for it. Your tools and the MCP protocol stay the same.

### Python or TypeScript

| Your server | Use |
|-------------|-----|
| A Python MCP server on stdio, which the host application starts | The Python bridge above (`protect_server`). Stdio has no HTTP layer, so every error is a tool result. |
| An MCP server on Streamable HTTP, in TypeScript or JavaScript | `@asap-protocol/mcp-auth`. Token errors are HTTP answers; grant errors are mostly tool results (see [Every response](#every-response)). |
| A Python MCP server on HTTP | Neither package covers it. |

### Install

The package needs Node.js 20 or later, and it serves MCP protocol revision `2026-07-28` only. It has two peer dependencies: `@modelcontextprotocol/server` 2.4.0 or a later 2.x release, and `@asap-protocol/client` 2.5.0 or a later 2.x release (it verifies Agent JWTs).

```bash
npm install @asap-protocol/mcp-auth @asap-protocol/client @modelcontextprotocol/server @modelcontextprotocol/node zod
```

`@modelcontextprotocol/node` mounts the server on `node:http`. `zod` describes the tool inputs. The package version is `0.1.0`: its API can change in a `0.Y.0` release.

### How a call is checked

The package works in two layers:

1. **The token layer** sits in front of the MCP handler (`auth.protect(handler)`). It reads the `Authorization` header and the MCP headers before the SDK reads the body. A missing or bad token gets HTTP 401. A request that the config does not allow gets HTTP 403. Any other request goes on to the SDK, and the tools find the verified caller (the *principal*) in `ctx.http.authInfo`.
2. **The grant layer** wraps each protected tool (`auth.protectTool(name, callback)`). It checks that the principal holds a grant for the tool's capability, and that the arguments meet the grant's constraints. If not, the tool returns an error result (`isError: true`), and your callback does not run.

The package accepts two kinds of token. This section starts with the first; [External OAuth 2.1 access tokens](#external-oauth-21-access-tokens) adds the second.

| Token | Sent by | Relation to MCP authorization (OAuth 2.1) |
|-------|---------|-------------------------------------------|
| ASAP Agent JWT | An ASAP agent. It signs a new token for each request with its own key. | None. It is a custom auth strategy, which MCP allows. |
| OAuth 2.1 access token (a JWT) | A standard MCP client, after it signs in at your authorization server | Your server is the resource server of MCP authorization (OAuth 2.1). |

### A protected server with Agent JWTs

This server has one public tool, `echo`, and one protected tool, `secure_action`. Save it as `server.ts`:

```ts
import { createServer } from "node:http";

import type { AgentKeyResolver, PublicJwk } from "@asap-protocol/client";
import { createMcpAuthMiddleware, type CapabilityGrant } from "@asap-protocol/mcp-auth";
import { toNodeHandler } from "@modelcontextprotocol/node";
import { createMcpHandler, McpServer } from "@modelcontextprotocol/server";
import * as z from "zod";

// The public URL of this MCP endpoint. Each Agent JWT must name it in its aud claim.
const MCP_URL = "https://mcp.example.com/mcp";

// The agents that you accept, by agent id. Load them from your own records.
interface KnownAgent {
  publicJwk: PublicJwk; // the agent's Ed25519 public key, as a JWK
  hostThumbprint: string; // the host that the agent belongs to
  active: boolean; // false when you revoke the agent or its host
  grants: CapabilityGrant[]; // the capabilities that the agent may use
}
const agents = new Map<string, KnownAgent>();

// The package checks the signature with the key that you return. You decide whether
// the agent is known, usable, and bound to the host named in the token.
const resolveAgent: AgentKeyResolver = async ({ agentId, hostThumbprint }) => {
  const agent = agents.get(agentId);
  if (agent === undefined) return undefined;
  return { publicKey: agent.publicJwk, usable: agent.active && agent.hostThumbprint === hostThumbprint };
};

const auth = createMcpAuthMiddleware({
  agentJwt: { audience: MCP_URL, resolveAgent },
  publicTools: ["echo"],
  toolCapabilities: { secure_action: "secure_action" }, // every protected tool → its capability
  agentGrants: (principal) => agents.get(principal.agentId)?.grants ?? [],
  onInternalError: (error) => console.error(error), // the package itself never logs
});

// Revision 2026-07-28 has no session: the SDK calls this once per HTTP request.
function buildServer(): McpServer {
  const server = new McpServer({ name: "my-mcp-server", version: "1.0.0" });
  server.registerTool(
    "echo",
    { description: "Public: no token needed", inputSchema: z.object({ message: z.string() }) },
    async ({ message }) => ({ content: [{ type: "text", text: message }] }),
  );
  server.registerTool(
    "secure_action",
    { description: "Protected: needs an Agent JWT and a grant", inputSchema: z.object({ action: z.string() }) },
    auth.protectTool("secure_action", async ({ action }) => ({ content: [{ type: "text", text: `executed: ${action}` }] })),
  );
  return server;
}

// legacy: "reject" serves MCP 2026-07-28 only. The token layer relies on it.
const app = auth.protect(createMcpHandler(buildServer, { legacy: "reject" }));
createServer(toNodeHandler(app)).listen(Number(process.env.PORT ?? 8787));
```

What each part does:

- **`audience`** is required. An Agent JWT whose `aud` claim does not hold this value gets HTTP 401. Use the public URL of the MCP endpoint.
- **`publicTools`** lists the tools that a request with no token may call.
- **`toolCapabilities`** maps every protected tool to its ASAP capability. A tool that is in neither list cannot be called (see [Deny by default](#deny-by-default)).
- **`agentGrants`** returns the grants of a verified agent. With no `agentGrants`, no agent has a grant. For a fixed map, `staticAgentGrants({ "<agent id>": [...] })` from the same package does the same job.
- **`protectTool`** wraps the callback of each tool in `toolCapabilities`. Register it under the same name. It throws a `TypeError` for a name that is in `publicTools` or not in `toolCapabilities`. A listed tool that you register **without** `protectTool` runs with no grant check.
- **`legacy: "reject"`** keeps the server on revision `2026-07-28`. The token layer trusts the `Mcp-Method` and `Mcp-Name` headers, and the SDK checks them against the body only on that revision.
- **`onInternalError`** gets failures that are not about the token, such as a resolver that throws. It never gets the token.

A grant's constraints work as in the Python bridge. A plain value must match exactly. `max` and `min` limit a number. `in` lists the allowed values, and `not_in` the forbidden ones. A grant `{ capability: "secure_action", constraints: { action: { not_in: ["drop-tables"] } } }` allows any `action` except `drop-tables`.

#### The agent resolver

The package checks the Agent JWT itself: the type (`typ: agent+jwt`), the Ed25519 signature, `aud`, `exp`, `iat`, and `jti`. It does not know your agents, so it asks `resolveAgent`:

- The resolver gets the token's `sub` as `agentId` and its `iss` as `hostThumbprint` (the key thumbprint of the agent's host).
- Return `undefined` for an agent that you do not know.
- Otherwise return the agent's Ed25519 public key (a JWK, typed `PublicJwk`; it needs no DOM lib) and `usable`. Return `usable: false` when the agent or its host is revoked, expired, or pending, or when the agent does not belong to that host. The package does not check the host binding: your resolver must.
- The resolver runs **before** the signature check, with values that nobody has verified yet. Keep it a read-only lookup. Do not record "last used" times or other state in it.
- If the resolver throws, the caller gets HTTP 500 with no detail, and `onInternalError` gets the error.

This package does not register agents. The records can come from the agent store of your ASAP server, or from a list that you load at start.

An unknown agent, an agent that is not usable, a key that is not Ed25519, and a bad signature get the same answer: `asap:invalid_token: agent or signature not accepted`. So a caller cannot learn whether an agent exists. Set `agentJwt.onTokenRefused` to log the exact reason.

#### One token per request

An Agent JWT lives 60 seconds and works for one request. The server refuses a token whose `exp` is more than 120 seconds after the current time or after `iat`. A *replay cache* remembers the ID (`jti`) of each accepted token for as long as the token is valid, and refuses it a second time.

The default replay cache is in memory, in one process. It does not survive a restart, and other processes do not see it. If several server instances share one audience, give them one shared cache: implement `JtiReplayCache` on a shared store and pass it as `agentJwt.replayCache`. When the in-memory cache holds 10 000 live records (or 1 000 for one agent), new Agent JWTs get HTTP 503 until records expire.

### Call the server from a client

#### With the MCP SDK client

Install `@asap-protocol/client` and `@modelcontextprotocol/client`. `agent` is the agent's ASAP identity, from `createAgent` or `resumeAgent` in [`@asap-protocol/client`](../sdks/typescript.md#identity-and-storage). The server operator needs its `agentId`, `publicJwk`, and `hostThumbprint`.

```ts
import type { AgentContext } from "@asap-protocol/client";
import { Client, StreamableHTTPClientTransport } from "@modelcontextprotocol/client";

const MCP_URL = "https://mcp.example.com/mcp";

export async function callSecureAction(agent: AgentContext, action: string) {
  const transport = new StreamableHTTPClientTransport(new URL(MCP_URL), {
    // Called before every HTTP request, so each request carries a new single-use Agent JWT.
    authProvider: { token: () => agent.signAgentJwt({ aud: MCP_URL, capabilities: ["secure_action"] }) },
  });
  const client = new Client({ name: "my-agent", version: "1.0.0" }, { versionNegotiation: { mode: { pin: "2026-07-28" } } });
  await client.connect(transport);
  try {
    return await client.callTool({ name: "secure_action", arguments: { action } });
  } finally {
    await client.close();
  }
}
```

- **`capabilities`** in the token must hold the tool's capability. The server checks this claim first, then the grant.
- **`versionNegotiation`** must pin `2026-07-28` (or be `"auto"`). In its default mode, the SDK client sends the 2025 `initialize` handshake, which the server refuses.
- When it connects, the client sends `server/discover`. Then it sends the call. Each request gets its own token.
- On HTTP 401 the client throws `UnauthorizedError` and does not retry.

#### With plain HTTP

Revision `2026-07-28` has no `initialize` handshake and no session, so one POST calls one tool. Mint a new Agent JWT for each POST, with `agent.signAgentJwt(...)` in TypeScript or `create_agent_jwt` in Python.

```bash
curl -s "$MCP_URL" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H 'MCP-Protocol-Version: 2026-07-28' \
  -H 'Mcp-Method: tools/call' \
  -H 'Mcp-Name: secure_action' \
  -H "Authorization: Bearer $AGENT_JWT" \
  --data '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"secure_action","arguments":{"action":"deploy"},"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{}}}}'
```

The answer from the server above:

```json
{"result":{"content":[{"type":"text","text":"executed: deploy"}],"resultType":"complete","_meta":{"io.modelcontextprotocol/serverInfo":{"name":"my-mcp-server","version":"1.0.0"}}},"jsonrpc":"2.0","id":1}
```

- The headers must agree with the body: `Mcp-Method` is the JSON-RPC method, and `Mcp-Name` is the tool name. If they do not agree, the SDK answers HTTP 400 with JSON-RPC error `-32020`.
- Both `_meta` keys are required (else HTTP 400, `-32602`). `io.modelcontextprotocol/clientInfo` is optional.
- `Content-Type` must be `application/json` (else HTTP 415).
- Keep the `Accept` header: a tool that sends a notification before its result answers with an event stream (SSE).
- A call to a public tool needs no `Authorization` header.

### External OAuth 2.1 access tokens

A standard MCP client has no Agent JWT. It signs in at an authorization server, gets an OAuth 2.1 access token, and sends that token on each request. With `externalTokens`, your server accepts such a token when it is a JWT from an authorization server that you list. Your server is then the resource-server side of MCP authorization (OAuth 2.1). The package does not run an authorization server and does not issue tokens.

```ts
import { createServer } from "node:http";

import { createMcpAuthMiddleware } from "@asap-protocol/mcp-auth";
import { toNodeHandler } from "@modelcontextprotocol/node";
import { createMcpHandler, getOAuthProtectedResourceMetadataUrl, McpServer } from "@modelcontextprotocol/server";
import * as z from "zod";

const MCP_URL = new URL("https://mcp.example.com/mcp");
const ISSUER = "https://auth.example.com"; // your authorization server
const resourceMetadataUrl = getOAuthProtectedResourceMetadataUrl(MCP_URL);
const noDrop = { action: { not_in: ["drop-tables"] } };

const auth = createMcpAuthMiddleware({
  externalTokens: {
    issuers: [
      {
        issuer: ISSUER,
        audience: MCP_URL.href,
        jwksUri: "https://auth.example.com/.well-known/jwks.json",
        grantRules: [
          { scope: "actions:run", capability: "secure_action", constraints: noDrop },
          { claim: "groups", value: "deployers", capability: "secure_action", constraints: noDrop },
        ],
      },
    ],
  },
  publicTools: ["echo"],
  toolCapabilities: { secure_action: "secure_action" },
  resourceMetadataUrl, // required with externalTokens
  onInternalError: (error) => console.error(error),
});

function buildServer(): McpServer {
  const server = new McpServer({ name: "my-mcp-server", version: "1.0.0" });
  server.registerTool(
    "echo",
    { description: "Public: no token needed", inputSchema: z.object({ message: z.string() }) },
    async ({ message }) => ({ content: [{ type: "text", text: message }] }),
  );
  server.registerTool(
    "secure_action",
    {
      description: "Protected: needs an access token that gives a grant",
      inputSchema: z.object({ action: z.string() }),
      scopeChallenge: auth.scopeChallenge, // a missing grant that a scope can give: HTTP 403
    },
    auth.protectTool("secure_action", async ({ action }) => ({ content: [{ type: "text", text: `executed: ${action}` }] })),
  );
  return server;
}

// RFC 9728 protected-resource metadata: it tells MCP clients which authorization server to use.
const metadata = {
  resource: MCP_URL.href,
  authorization_servers: [ISSUER],
  scopes_supported: ["actions:run"],
  bearer_methods_supported: ["header"],
};
const mcp = auth.protect(createMcpHandler(buildServer, { legacy: "reject" }));
const app = {
  fetch: async (request: Request) =>
    request.method === "GET" && new URL(request.url).pathname === new URL(resourceMetadataUrl).pathname
      ? Response.json(metadata)
      : mcp.fetch(request),
};
createServer(toNodeHandler(app)).listen(Number(process.env.PORT ?? 8787));
```

What each part does:

- **`issuers`** is the allowlist of authorization servers. A token from any other issuer gets HTTP 401. No mode accepts any issuer.
- **`audience`** is required: the token's `aud` claim must hold this value (RFC 8707). A token for another server gets HTTP 401, and the package never passes it on.
- **`jwksUri`** is where the package fetches the issuer's public keys (see [Key fetch and SSRF](#key-fetch-and-ssrf)). Take it from the `jwks_uri` in your authorization server's metadata. Or set `jwks: { keys: [...] }` instead, and nothing is fetched. Set one of the two. The token must be signed with `EdDSA`, `RS256`, or `ES256`.
- **`grantRules`** turn a token into grants, deny by default. A `scope` rule matches one entry of the space-separated `scope` claim. A `claim` rule matches a top-level claim that is exactly `value`, or a list that holds `value`. Each rule gives one `capability`, with optional `constraints`. Two rules for one capability must have the same constraints, or the config throws a `TypeError`. A scope or claim that no rule names gives nothing.
- **`resourceMetadataUrl`** is required with `externalTokens` (else a `TypeError`). It is the URL of your RFC 9728 *protected-resource metadata*: a small JSON document that tells an MCP client which authorization server to use. Every 401 and 403 challenge points to it. Serve the document yourself, as the last lines of the code do.
- **`scopeChallenge`** on each protected tool turns a missing grant into HTTP 403 `insufficient_scope`, with the scope that gives the grant. The SDK client then throws `InsufficientScopeError`, and an MCP client can ask for that scope. Without `scopeChallenge`, a missing grant is a tool result.

An access token has no replay check: the client sends the same token on each request until it expires. A tool finds the caller in `ctx.http?.authInfo?.extra?.principal`. For an access token it is `{ kind: "external", issuer, subject, scopes, grants }`; for an Agent JWT, `{ kind: "agent_jwt", agentId, hostThumbprint, capabilities }`.

To accept both kinds of token, put `agentJwt` and `agentGrants` from the first server in the same config. The token's `typ` header picks the check: `agent+jwt` goes to the Agent JWT check, and any other token to the access-token check. A token that one check refuses is never tried with the other.

#### Key fetch and SSRF

A `jwksUri` must be an `https` URL with no user name or password. The package fetches only that URL: no value in a token (`jku`, `x5u`, or the `iss` URL) adds or changes a fetch target. The fetch:

- follows a redirect only to the same origin, at most three times;
- stops after 5 seconds or 64 KiB;
- keeps only RSA, EC, and OKP public keys, and caches them for up to 10 minutes;
- fetches again for an unknown key ID (`kid`), but at most once every 30 seconds, so random key IDs cannot cause a flood of fetches.

Change these limits in `externalTokens.jwksFetch` (`timeoutMs`, `maxBytes`, `maxAgeMs`, `refreshCooldownMs`).

What the package does **not** do: in Node.js, `fetch` resolves the host name itself, so the package cannot check or pin the IP address behind the name. If the name resolves to a private address or a cloud metadata address, the package fetches it. The protection is that you choose the URL. Where that is not enough, set a static `jwks`, or limit outgoing traffic with an egress policy.

If the keys cannot be fetched, the caller gets HTTP 503 with `Retry-After`. The token may be fine, so the client can try again later.

### Deny by default

The token layer decides from the headers alone. Each request must send `MCP-Protocol-Version: 2026-07-28`.

**With no `Authorization` header**, a request passes only when it is one of these. Any other request gets HTTP 401 `asap:auth_required`.

- `tools/call` for a tool in `publicTools`
- `tools/list` (it lists every tool, the protected ones too)
- `server/discover`

**With a token**, the package verifies the token first, also for a public tool. A bad token gets HTTP 401. Then the request passes only when it is one of these. Any other request gets HTTP 403 `asap:capability_denied`, and no handler runs.

- `tools/call` for a tool in `publicTools` or `toolCapabilities`
- `tools/list` or `server/discover`
- a method in `allowMethods`

So `ping`, `resources/*`, `prompts/*`, and every other method need `allowMethods`, for example `allowMethods: ["ping", "resources/list", "resources/read"]`. A method in `allowMethods` runs for any verified token, with no grant check. `allowMethods` opens nothing to a request with no token, and it cannot list `tools/call`.

`protect` also refuses a request that the SDK would serve as a 2025-era request: HTTP 401 with no token, 403 with one.

#### Notifications

A JSON-RPC notification (a message with no `id`) gets the same refusal by default: HTTP 401 with no token, 403 with one, and no handler runs. A notification has the same headers as a request, so `protect` reads the body to tell them apart. A notification passes only when the token verifies, its method is in `allowMethods`, and the client sends that method in the `Mcp-Method` header. Then it reaches only the server's handler for that notification, never a tool.

The MCP SDK client 2.4, in revision `2026-07-28`, sends one notification on its own:

| Notification | When the SDK client sends it | What a refusal does | To let it through |
|--------------|------------------------------|---------------------|-------------------|
| `notifications/cancelled` | Only when it closes a `subscriptions/listen` subscription (which itself needs `allowMethods: ["subscriptions/listen"]`) | The client passes the 403 to `client.onerror` and goes on. The server loses nothing: the client also closes the subscription's stream, and that ends the subscription. | The SDK client sends no `Mcp-Method` header with a notification, so no `allowMethods` entry opens it. A client that sends `Mcp-Method: notifications/cancelled` needs `allowMethods: ["notifications/cancelled"]`. |

It sends no notification when it connects, calls, receives progress, or closes. It cancels a call, and ends a call that timed out, by closing that call's HTTP stream: the tool sees `ctx.mcpReq.signal` abort. `createMcpHandler` builds a new server for each POST, so a `notifications/cancelled` in its own POST cannot cancel a call that runs in another one.

Two things stay with you. Wrap every tool in `toolCapabilities` with `protectTool`. And put in `allowMethods` only methods that any verified caller may use.

### Every response

The token layer answers with HTTP. The grant layer answers with a tool result.

- An **HTTP answer** means that no tool ran. The ASAP code is in the `error_description` of the JSON body. It is also in the `WWW-Authenticate` header, except when there is no token.
- A **tool result** comes back with HTTP 200. The JSON-RPC result has `isError: true` and one text item, `<code>` or `<code>: <detail>`. The Python bridge returns the same payload.

| Case | Answer | ASAP code | Header |
|------|--------|-----------|--------|
| No token, and the request is not a public one (a notification never is) | HTTP 401. Body `{"error":"invalid_token","error_description":"asap:auth_required"}` | `asap:auth_required` | `WWW-Authenticate: Bearer resource_metadata="<url>"` |
| `Authorization` is not `Bearer <token>` | HTTP 401. Body `{"error":"invalid_token","error_description":"<ASAP code>"}` | `asap:invalid_token: malformed Authorization header` | `WWW-Authenticate: Bearer error="invalid_token", error_description="<ASAP code>", resource_metadata="<url>"` |
| Bad, expired, wrong-audience, or replayed token | As in the row above | `asap:invalid_token: <reason>` | As in the row above |
| Verified token, but a tool or method that the config does not declare, a notification that `allowMethods` does not list, no `MCP-Protocol-Version: 2026-07-28`, or a 2025-era request | HTTP 403. Body `{"error":"insufficient_scope","error_description":"asap:capability_denied"}` | `asap:capability_denied` | `WWW-Authenticate: Bearer error="insufficient_scope", error_description="asap:capability_denied", resource_metadata="<url>"`, with no `scope` |
| Access token, no grant for the tool, and a `scope` rule gives one (`scopeChallenge` set) | HTTP 403, same body | `asap:capability_denied` | The same, with `scope="<scope>"` before `resource_metadata` |
| The issuer's keys cannot be fetched | HTTP 503. Body `{"error":"temporarily_unavailable","error_description":"Authentication service unavailable"}` | None | `Retry-After: <seconds>`, no `WWW-Authenticate` |
| The in-memory replay cache is full of live records | HTTP 503, same body | None | `Retry-After: <seconds>` |
| `resolveAgent` or the replay cache throws | HTTP 500. Body `{"error":"server_error","error_description":"Internal Server Error"}` | None | None |
| A protected tool runs with no verified caller (a second guard: the token layer normally answers first) | Tool result | `asap:auth_required` | — |
| The Agent JWT's `capabilities` claim does not hold the tool's capability | Tool result | `asap:capability_denied: JWT capabilities claim does not include '<capability>'` | — |
| No grant for the capability: an Agent JWT, or an access token when no `scope` rule gives it | Tool result | `asap:capability_denied: no active grant for capability '<capability>'` | — |
| `agentGrants` throws | Tool result | `asap:capability_denied: grant lookup failed` | — |
| The `protectTool` callback is registered under another tool's name | Tool result | `asap:capability_denied: the request is not a call to '<name>'` | — |
| The arguments break a constraint | Tool result | `asap:constraint_violation: <field>: <reason>` | — |

`<url>` is your `resourceMetadataUrl`. Without it, the challenge has no `resource_metadata` parameter, so the no-token challenge is a plain `Bearer`. `<reason>` is a fixed text, such as `malformed token` (a token that cannot be decoded, with either kind of token and in every config), `token expired`, `audience mismatch`, or `jti replay detected`. No answer holds the token or a claim value. For a 500 or a 503, `onInternalError` gets the cause.

The two kinds of token differ in one case. With an Agent JWT, a missing grant is always a tool result, as in the Python bridge. With an access token, it is HTTP 403 when a scope can give the grant, so that a standard MCP client can ask for that scope; otherwise it is a tool result. A constraint violation is a tool result on both paths, because it depends on the arguments.

Some answers come from the MCP SDK, not from this package: HTTP 400 when the headers disagree with the body (`-32020`) or a `_meta` key is missing (`-32602`), and HTTP 415 for a wrong `Content-Type`.

### Limits

- **Agent JWTs are not MCP authorization.** With Agent JWTs alone, the package is a custom auth strategy, which MCP allows. It is not an implementation of MCP authorization (OAuth 2.1). A client that knows only MCP authorization cannot get an Agent JWT.
- **Access tokens: resource server only, JWTs only.** With `externalTokens`, your server is the resource-server side of MCP authorization (OAuth 2.1). The package does not run an authorization server, issue tokens, or serve authorization-server metadata. It accepts JWT access tokens, not opaque ones.
- **One transport, one revision.** Streamable HTTP and MCP revision `2026-07-28` only. For stdio, use the Python bridge.
- **The replay cache is per process** (see [One token per request](#one-token-per-request)).
- **Tokens only.** The package does not check the `Host` or `Origin` header. For a server on a local machine, put the SDK's `hostHeaderValidationResponse` and `originValidationResponse` in front of it, as the example does.
- **`tools/list` shows every tool** to every caller. Hiding tools is not supported.

### Runnable example

[`examples/mcp_auth_http/`](https://github.com/asap-protocol/asap-protocol/tree/main/examples/mcp_auth_http) is a complete server with `echo` and `secure_action`. It creates a demo agent and a local test issuer at each start. Its tests run the five cases (success, no token, bad token, no grant, constraint violation) through a real HTTP server with the SDK client. From the repository root:

```bash
pnpm install
pnpm --filter @asap-protocol/client run build && pnpm --filter @asap-protocol/mcp-auth run build
pnpm --filter example-mcp-auth-http test
```

`pnpm --filter example-mcp-auth-http start` runs it on `http://127.0.0.1:8787/mcp`. `pnpm --filter example-mcp-auth-http start -- --compliance` also hands out fresh valid test tokens at `POST /compliance/probe-tokens`, so that a remote compliance checker can run its pass and fail cases. **Compliance mode is for tests only**: anyone who can reach the server can get a token. Never run it where an untrusted client can reach it. The [example README](https://github.com/asap-protocol/asap-protocol/blob/main/examples/mcp_auth_http/README.md) has the details.

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
