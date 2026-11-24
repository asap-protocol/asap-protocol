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
| External OAuth 2.1 access token | Verifies the token against an issuer allowlist and the server's audience, and maps its scopes or claims to ASAP capabilities. On this path the server is the resource server of MCP authorization (OAuth 2.1). |

Set `agentJwt`, `externalTokens`, or both. With both, the token's `typ` header picks one verifier: `agent+jwt` goes to the Agent JWT verifier, and any other token goes to the external verifier. A token that its verifier refuses is never tried with the other one.

**Deny by default.** A request with no token reaches only the tools in `publicTools`, `tools/list`, and `server/discover`. A request with a verified token reaches only those, the tools in `toolCapabilities`, and the methods in `allowMethods`; anything else gets HTTP 403 before any handler runs. A tool in `toolCapabilities` runs only when `protectTool` finds a grant for its capability. With no `agentGrants`, no Agent JWT principal has a grant, and an external token has only the grants that its issuer's `grantRules` map. Two things stay with you: wrap every tool in `toolCapabilities` with `protectTool` (a listed tool that is not wrapped runs with no grant check; `protect` cannot stop that call, but reports it to `onInternalError` when it returns a result), and list in `allowMethods` only methods whose handlers may run for any verified token.

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
  toolCapabilities: { secure_action: "secure_action" }, // every protected tool (deny by default)
  resourceMetadataUrl: "https://mcp.example.com/.well-known/oauth-protected-resource/mcp",
  onInternalError: (error) => logger.error(error), // the package itself never logs
});

// legacy: "reject" serves MCP 2026-07-28 only, as a second guard for the public-tool rule.
const app = auth.protect(createMcpHandler(buildServer, { legacy: "reject" }));
createServer(toNodeHandler(app)).listen(8787);
```

`resolveAgent` returns the agent's Ed25519 public JWK and whether the agent is usable (see `verifyAgentJwt` in `@asap-protocol/client`). A tool reads the verified agent as `ctx.http?.authInfo?.extra?.principal`.

**Host binding.** `resolveAgent` gets the token's `sub` (`agentId`) and `iss` (`hostThumbprint`). The package checks that the agent's key signed the token, so `principal.hostThumbprint` is the `iss` that the agent signed. The package does **not** check that the agent belongs to that host, or that the host is not revoked: your resolver must, and return `usable: false` when either fails. The example's resolver shows the check.

- **Requests with no token.** A request without an `Authorization` header passes with no auth info only when it sends `MCP-Protocol-Version: 2026-07-28` and either calls a public tool (`Mcp-Method: tools/call`, `Mcp-Name: <tool>`) or is `tools/list` or `server/discover`. Any other request with no token gets HTTP 401. The rule reads headers only, before the SDK parses the body.
- **Notifications.** A JSON-RPC notification (no `id`) has the same headers as a request, so `protect` reads the body. It refuses a notification unless the token verifies and `allowMethods` lists its method (401 with no token, 403 with one); the client must also send that method in `Mcp-Method`. The MCP SDK client 2.4 sends none on its own in revision `2026-07-28`, except `notifications/cancelled` when it closes a `subscriptions/listen` subscription, and it sends that one with no `Mcp-Method` header. Refusing it loses nothing: closing the subscription's stream ends it.
- **2025-era requests.** The header rule holds because the SDK checks `Mcp-Method` and `Mcp-Name` against the body on a `2026-07-28` request. So `protect` also runs the SDK's `isLegacyRequest` (on a copy of the body) and refuses any request that the SDK would serve as 2025-era: 401 with no token, 403 with one. Mount the handler with `legacy: "reject"` as well.
- **Requests with a token.** The token is always verified, also for a public tool. Then the same header rule applies, deny by default: the request must send `MCP-Protocol-Version: 2026-07-28`, and it may call only a tool in `publicTools` or `toolCapabilities`, `tools/list`, `server/discover`, or a method in `allowMethods` (for example `"resources/read"`). Anything else gets HTTP 403 before any handler runs. A method in `allowMethods` runs with no grant check, and every tool in `toolCapabilities` must be wrapped with `protectTool`.
- **Lifetime.** `signAgentJwt` (and Python `create_agent_jwt`) mint an Agent JWT that lives 60 seconds. The verifier refuses, with `token lifetime too long`, a token whose `exp` is more than 120 seconds (the 60-second lifetime plus 60 seconds of clock skew) after now or after `iat`.
- **Single use.** Each Agent JWT is accepted once, so a client mints one per request. With the SDK client: `new StreamableHTTPClientTransport(url, { authProvider: { token: () => agent.signAgentJwt({ aud }) } })`.
- **What the default replay cache guarantees.** It is one `InMemoryJtiReplayCache` per middleware, in one process. It refuses a `jti` again for the later of 90 seconds and the token's `exp`, so for as long as the token can verify, and it never evicts a live record. When it holds 10 000 live records, or 1 000 for one agent, it refuses new Agent JWTs with HTTP 503 instead. It does not survive a restart, and other processes do not see it: several server instances behind one audience need one shared `JtiReplayCache` (pass it as `agentJwt.replayCache`) whose `checkAndRecord` keeps each record until the `expiresAt` it receives.

| Case | HTTP answer |
|------|-------------|
| No token | 401, `WWW-Authenticate: Bearer resource_metadata="<url>"` (or `Bearer`), body `{"error":"invalid_token","error_description":"asap:auth_required"}` |
| Bad, expired, wrong-audience, or replayed token | 401, `WWW-Authenticate: Bearer error="invalid_token", error_description="asap:invalid_token: <reason>", resource_metadata="<url>"`, the same text in the body |
| A verified token for a tool or method that the config does not declare | 403, `WWW-Authenticate: Bearer error="insufficient_scope", error_description="asap:capability_denied", resource_metadata="<url>"`, body `{"error":"insufficient_scope","error_description":"asap:capability_denied"}`. No `scope` parameter: no scope grants an undeclared method |
| The replay cache is full of live records (`JtiReplayCacheFullError`) | 503, body `{"error":"temporarily_unavailable","error_description":"Authentication service unavailable"}`, `Retry-After: <seconds until enough records expire>`, no `WWW-Authenticate`; the error goes to `onInternalError`. The token may be fine |
| The resolver or the replay cache throws anything else | 500, body `{"error":"server_error","error_description":"Internal Server Error"}`; the error goes to `onInternalError` |

`<reason>` is a fixed string, such as `malformed token` (a token that cannot be decoded; the same text with either verifier), `audience mismatch`, or `jti replay detected`. No answer holds the token or any claim value. An unknown agent, an agent that is not usable, a key that is not Ed25519, and a bad signature share one reason, `agent or signature not accepted`, so the answer does not tell whether an agent exists or is revoked. `agentJwt.onTokenRefused` receives the detailed reason (an `AgentJwtFailureReason`) for your logs.

## Grants (tool results)

`protectTool(name, callback)` wraps a tool callback. Pass the result as the third argument of `registerTool`, under the same name. `name` must be in `toolCapabilities` and not in `publicTools`; otherwise `protectTool` throws a `TypeError`. Register a public tool without `protectTool`.

```ts
import { createMcpAuthMiddleware, staticAgentGrants } from "@asap-protocol/mcp-auth";

const auth = createMcpAuthMiddleware({
  agentJwt: { audience, resolveAgent },
  toolCapabilities: { secure_action: "secure_action" }, // tool name → capability, for every protected tool
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

1. The request's `Mcp-Name` header is `name`. The SDK has checked that header against the body, so this holds unless the callback was registered under another tool's name. If not: `asap:capability_denied: the request is not a call to '<name>'`, and the error goes to `onInternalError`. A call with no HTTP request (stdio) fails the same way.
2. The request has a verified ASAP principal. If not: `asap:auth_required`. The token layer normally refuses such a request first; this check is a second guard.
3. The token's `capabilities` claim holds the tool's capability. If not: `asap:capability_denied: JWT capabilities claim does not include '<capability>'`.
4. `agentGrants` returns a grant for that capability. If not: `asap:capability_denied: no active grant for capability '<capability>'`. With no `agentGrants`, no agent has a grant. If the resolver throws, the detail is `grant lookup failed`, and the error goes to `onInternalError`.
5. The call arguments meet the grant's constraints. If not: `asap:constraint_violation: <field>: <reason>`.

Constraints work as in Python `validate_constraints`: a plain value is an exact match, and `max`, `min`, `in`, and `not_in` are operators. The check uses the arguments that the SDK passes to the tool, after the `inputSchema` has parsed them. A tool with no `inputSchema` gets no arguments, so a grant with constraints always fails on it. Where Python passes or raises, this package returns a violation: for example, a number that is not finite for `max` or `min`, or a number argument for `in` on a string.

For a tool with no `inputSchema`, write the callback with its `ctx` parameter: `auth.protectTool("name", async (ctx) => ...)`.

## Usage (external OAuth 2.1 tokens)

```ts
import { createMcpAuthMiddleware } from "@asap-protocol/mcp-auth";
import { createMcpHandler, getOAuthProtectedResourceMetadataUrl, oauthMetadataResponse } from "@modelcontextprotocol/server";

const resource = new URL("https://mcp.example.com/mcp");
const auth = createMcpAuthMiddleware({
  externalTokens: {
    issuers: [{
      issuer: "https://auth.example.com",
      audience: resource.href, // required (RFC 8707)
      jwksUri: "https://auth.example.com/.well-known/jwks.json", // or jwks: { keys: [...] }
      grantRules: [
        { scope: "actions:run", capability: "secure_action", constraints: { action: { not_in: ["drop"] } } },
        { claim: "groups", value: "auditors", capability: "audit_log" },
      ],
    }],
  },
  toolCapabilities: { secure_action: "secure_action" },
  resourceMetadataUrl: getOAuthProtectedResourceMetadataUrl(resource), // required with externalTokens
});

server.registerTool(
  "secure_action",
  { inputSchema: z.object({ action: z.string() }), scopeChallenge: auth.scopeChallenge },
  auth.protectTool("secure_action", async ({ action }) => ({ content: [{ type: "text", text: action }] })),
);

// RFC 9728 metadata from the SDK helper, beside the protected MCP endpoint.
const mcp = auth.protect(createMcpHandler(buildServer, { legacy: "reject" }));
const app = { fetch: async (request: Request) => oauthMetadataResponse(request, metadataOptions) ?? mcp.fetch(request) };
```

With `externalTokens`, `resourceMetadataUrl` is required (a `TypeError` otherwise): the server is then the resource server of MCP authorization, which must serve RFC 9728 metadata, and every 401 and 403 challenge points to it.

The verifier checks, in this order: the token is a JWT; its `typ` is not `agent+jwt` or `host+jwt`; its `alg` is `EdDSA`, `RS256`, or `ES256` (so `none` and HMAC fail before any key lookup); its unverified `iss` is on the allowlist (trailing slashes do not count); the signature verifies with **that issuer's** keys; `aud` holds the configured audience; `exp` and `nbf` hold; `sub` and `exp` are present. The token picks nothing: `jku`, `x5u`, and the `iss` URL are never fetched. There is no replay check, because a client sends the same access token on every request.

Grants come only from the issuer's `grantRules`, deny by default: a `scope` rule matches one token of the space-separated `scope` claim, and a `claim` rule matches a top-level claim that is the exact string `value` or a list that holds it. A tool reads the principal as `ctx.http?.authInfo?.extra?.principal` (`kind: "external"`, `issuer`, `subject`, `scopes`, `grants`).

| Case | HTTP answer |
|------|-------------|
| Refused token | 401, `asap:invalid_token: <reason>`. An issuer that is not allowlisted and a bad signature share one reason, `issuer or signature not accepted` |
| No grant for the tool, and a scope rule of the issuer gives one | 403, `WWW-Authenticate: Bearer error="insufficient_scope", error_description="asap:capability_denied", scope="<scope>", resource_metadata="<url>"` (needs `scopeChallenge` on the tool; without it, a tool result, and `onInternalError` gets an error that names the tool) |
| No grant, and no scope rule gives one | Tool result, `asap:capability_denied` |
| Constraint violated | Tool result, `asap:constraint_violation` |
| The issuer's JWKS cannot be fetched (network error, timeout, HTTP error, too large, redirect refused, not a JWKS), or the last fetch failed less than `refreshCooldownMs` ago | 503, body `{"error":"temporarily_unavailable","error_description":"Authentication service unavailable"}`, `Retry-After: <seconds until the next fetch is allowed>`, no `WWW-Authenticate`. The cause (host and reason) goes to `onInternalError` only. A client can retry later; the token may be fine. Python answers the same case with the same status and text |

### JWKS fetch and SSRF

A `jwksUri` must be `https` with no credentials. The fetch follows a redirect only on the same origin (at most three), stops after `timeoutMs` (default 5 s) and `maxBytes` (default 64 KiB), keeps only RSA, EC, and OKP public keys, and caches them for at most `maxAgeMs` (default 10 min). An unknown `kid` refreshes the keys, but at most one fetch runs per `refreshCooldownMs` (default 30 s), so random `kid` values cannot multiply fetches. Set these in `externalTokens.jwksFetch`.

What this does **not** give: in a JavaScript runtime, `fetch` resolves the host name itself, so the package cannot check or pin the IP address that the host resolves to, as the Python helper does. A host name that resolves to a private or metadata address is fetched. The protection is that operators choose the URLs: only the configured `jwksUri` is fetched, and no token value adds or changes a fetch target. Use a static `jwks` or an egress policy where that is not enough.

## Error codes

| Case | Result |
|------|--------|
| No token | HTTP 401, `asap:auth_required` |
| Invalid, expired, or wrong-audience token | HTTP 401, `asap:invalid_token` |
| Capability not granted | Tool result with `isError: true`, `asap:capability_denied`. On the external-token path, HTTP 403 `insufficient_scope` when a scope rule can give the grant. On both paths, HTTP 403 for a tool or method that the config does not declare |
| Constraint violated | Tool result with `isError: true`, `asap:constraint_violation` |

## Links

- Python bridge: [docs/adapters/mcp-auth-bridge.md](https://github.com/asap-protocol/asap-protocol/blob/main/docs/adapters/mcp-auth-bridge.md)
