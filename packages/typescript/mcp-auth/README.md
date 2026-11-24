# `@asap-protocol/mcp-auth`

> **Status:** in development for ASAP v2.6.0. Version `0.1.0`. The API can change in a `0.Y.0` release.

Middleware that protects an HTTP MCP server with ASAP identity and capability grants. It returns the same four `asap:*` error codes as the Python MCP Auth Bridge (`asap.mcp.auth`).

## Scope

- **SDK:** `@modelcontextprotocol/server` `^2.4` (peer dependency). MCP protocol revision `2026-07-28`.
- **Transport:** Streamable HTTP only. For stdio, use the Python bridge.
- **Runtime:** Node.js 20 or later. The core takes a web-standard `Request` and returns a `Response`, so it does not depend on a web framework.

## Two token types

| Token | What the package does |
|-------|-----------------------|
| ASAP Agent JWT | Verifies the token and maps the agent to its ASAP grants. MCP lets a client and a server agree on their own auth strategy; this is such a custom strategy. It is not MCP authorization. |
| External OAuth 2.1 access token | Verifies the token against an issuer allowlist and the server's audience, and maps its scopes to ASAP capabilities. This path follows MCP authorization (OAuth 2.1). |

A token never falls through from one verifier to the other.

## Usage (Agent JWT)

```ts
import { createServer } from "node:http";
import { createMcpAuthMiddleware } from "@asap-protocol/mcp-auth";
import { toNodeHandler } from "@modelcontextprotocol/node";
import { createMcpHandler } from "@modelcontextprotocol/server";

const auth = createMcpAuthMiddleware({
  agentJwt: {
    audience: "https://mcp.example.com/mcp", // required
    resolveAgent: async ({ agentId, hostThumbprint }) => lookUpAgent(agentId, hostThumbprint),
  },
  publicTools: ["echo"],
  resourceMetadataUrl: "https://mcp.example.com/.well-known/oauth-protected-resource/mcp",
  onInternalError: (error) => logger.error(error), // the package itself never logs
});

// legacy: "reject" serves MCP 2026-07-28 only, as a second guard for the public-tool rule.
const app = auth.protect(createMcpHandler(buildServer, { legacy: "reject" }));
createServer(toNodeHandler(app)).listen(8787);
```

`resolveAgent` returns the agent's Ed25519 public JWK and whether the agent is usable (see `verifyAgentJwt` in `@asap-protocol/client`). A tool reads the verified agent as `ctx.http?.authInfo?.extra?.principal`.

- **Requests with no token.** A request without an `Authorization` header passes with no auth info only when it sends `MCP-Protocol-Version: 2026-07-28` and either calls a public tool (`Mcp-Method: tools/call`, `Mcp-Name: <tool>`) or is `tools/list` or `server/discover`. Any other request with no token gets HTTP 401. The rule reads headers only, before the SDK parses the body.
- **Requests with a token.** The token is always verified, also for a public tool.
- **Single use.** An Agent JWT lives 60 seconds and is single-use, so a client mints one per request. With the SDK client: `new StreamableHTTPClientTransport(url, { authProvider: { token: () => agent.signAgentJwt({ aud }) } })`. The default replay cache is in memory; several server instances behind one audience need a shared `JtiReplayCache`.

| Case | HTTP answer |
|------|-------------|
| No token | 401, `WWW-Authenticate: Bearer resource_metadata="<url>"` (or `Bearer`), body `{"error":"invalid_token","error_description":"asap:auth_required"}` |
| Bad, expired, wrong-audience, or replayed token | 401, `WWW-Authenticate: Bearer error="invalid_token", error_description="asap:invalid_token: <reason>", resource_metadata="<url>"`, the same text in the body |
| The resolver or the replay cache throws | 500, body `{"error":"server_error","error_description":"Internal Server Error"}`; the error goes to `onInternalError` |

`<reason>` is a fixed string, such as `audience mismatch` or `jti replay detected`. No answer holds the token or any claim value.

## Grants (tool results)

`protectTool(name, callback)` wraps a tool callback. Pass the result as the third argument of `registerTool`:

```ts
import { createMcpAuthMiddleware, staticAgentGrants } from "@asap-protocol/mcp-auth";

const auth = createMcpAuthMiddleware({
  agentJwt: { audience, resolveAgent },
  toolCapabilities: { secure_action: "secure_action" }, // tool name → capability; default: the tool name
  agentGrants: staticAgentGrants({
    [agentId]: [{ capability: "secure_action", constraints: { action: { not_in: ["drop-tables"] } } }],
  }),
});

server.registerTool(
  "secure_action",
  { inputSchema: z.object({ action: z.string() }) },
  auth.protectTool("secure_action", async ({ action }) => ({ content: [{ type: "text", text: `executed: ${action}` }] })),
);
```

A call runs only if all of these are true. Otherwise the tool returns a `CallToolResult` with `isError: true`, the same payload as the Python bridge:

1. The request has a verified ASAP principal. If not: `asap:auth_required`. The token layer normally refuses such a request first; this check is a second guard.
2. The token's `capabilities` claim holds the tool's capability. If not: `asap:capability_denied: JWT capabilities claim does not include '<capability>'`.
3. `agentGrants` returns a grant for that capability. If not: `asap:capability_denied: no active grant for capability '<capability>'`. With no `agentGrants`, no agent has a grant. If the resolver throws, the detail is `grant lookup failed`, and the error goes to `onInternalError`.
4. The call arguments meet the grant's constraints. If not: `asap:constraint_violation: <field>: <reason>`.

Constraints work as in Python `validate_constraints`: a plain value is an exact match, and `max`, `min`, `in`, and `not_in` are operators. The check uses the arguments that the SDK passes to the tool, after the `inputSchema` has parsed them. A tool with no `inputSchema` gets no arguments, so a grant with constraints always fails on it. Where Python passes or raises, this package returns a violation: for example, a number that is not finite for `max` or `min`, or a number argument for `in` on a string. A public tool runs unchanged.

For a tool with no `inputSchema`, write the callback with its `ctx` parameter: `auth.protectTool("name", async (ctx) => ...)`.

## Error codes

| Case | Result |
|------|--------|
| No token | HTTP 401, `asap:auth_required` |
| Invalid, expired, or wrong-audience token | HTTP 401, `asap:invalid_token` |
| Capability not granted | Tool result with `isError: true`, `asap:capability_denied` (HTTP 403 `insufficient_scope` on the external-token path) |
| Constraint violated | Tool result with `isError: true`, `asap:constraint_violation` |

## Links

- Python bridge: [docs/adapters/mcp-auth-bridge.md](https://github.com/asap-protocol/asap-protocol/blob/main/docs/adapters/mcp-auth-bridge.md)
