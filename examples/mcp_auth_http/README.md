# Example: HTTP MCP server protected by `@asap-protocol/mcp-auth`

A Streamable HTTP MCP server on `@modelcontextprotocol/server` 2.x (MCP revision `2026-07-28`), protected by `@asap-protocol/mcp-auth`. It has one public tool, `echo`, and one protected tool, `secure_action`.

The server accepts two token types:

- **ASAP Agent JWT.** At each start, the server creates a demo ASAP host and agent in memory. The agent has one grant: capability `secure_action`, with the constraint that `action` is not `forbidden-action`. With an Agent JWT, the package is a custom auth strategy, which MCP allows; it is not MCP authorization (OAuth 2.1).
- **External OAuth 2.1 access token.** A local test issuer, `https://test-issuer.invalid`, is created at each start; the server gets its public keys as a static JWKS, so nothing is fetched. Its one grant rule maps the scope `secure_action:run` to `secure_action`, with the same constraint. On this path the server is the resource server of MCP authorization (OAuth 2.1). A token without the scope gets HTTP 403 `insufficient_scope`.

## Protected-resource metadata (RFC 9728)

MCP authorization says a protected MCP server must serve RFC 9728 metadata. The example serves it at `/.well-known/oauth-protected-resource/mcp`, with no token. `resource` is the MCP URL, `authorization_servers` lists only the issuer that the server accepts tokens from, and `bearer_methods_supported` is `["header"]`. Every 401 links the document with `resource_metadata="<url>"`.

The built-in test issuer is a local key pair for the tests and the smoke run. It has no authorization endpoint, so a standard MCP client cannot get a token from it. A real deployment sets its own issuer, its keys (`jwks` or `jwksUri`), and its grant rules. The example serves no authorization-server metadata and does not invent authorization or token endpoints.

With `startExampleServer({ externalTokens: false })` the server accepts Agent JWTs only. Then it serves no metadata document (the path answers 404), and the 401 challenge has no `resource_metadata`, because Agent JWTs alone are a custom auth strategy, not MCP authorization.

The server serves only the `2026-07-28` revision (`legacy: "reject"`), and it accepts only local `Host` and `Origin` values.

## Smoke test

```bash
pnpm --filter example-mcp-auth-http test
```

`test/protected.test.ts` starts the server on a free local port and runs the five cases of MCP-TS-002 with the SDK client, pinned to `2026-07-28`:

| Case | Layer | Answer |
|------|-------|--------|
| Granted call | Tool result | `executed: <action>` |
| No token | HTTP | 401, `asap:auth_required`, `resource_metadata` in the challenge |
| Invalid token | HTTP | 401, `asap:invalid_token: <reason>` |
| Token without the capability | Tool result | `isError: true`, `asap:capability_denied` |
| `action: "forbidden-action"` | Tool result | `isError: true`, `asap:constraint_violation` |

It also runs the external-token path: the same access token works on two calls, and a token without `secure_action:run` gets HTTP 403 `insufficient_scope` with `asap:capability_denied`.

## Run

```bash
pnpm install
pnpm --filter @asap-protocol/client run build && pnpm --filter @asap-protocol/mcp-auth run build
pnpm --filter example-mcp-auth-http start   # MCP endpoint: http://127.0.0.1:8787/mcp
```

## Call a tool without an MCP SDK

The `2026-07-28` revision has no `initialize` handshake and no session. One POST calls a tool. The public tool needs no token:

```bash
curl -s http://127.0.0.1:8787/mcp \
  -H 'Content-Type: application/json' \
  -H 'MCP-Protocol-Version: 2026-07-28' \
  -H 'Mcp-Method: tools/call' \
  -H 'Mcp-Name: echo' \
  --data '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"echo","arguments":{"message":"hi"},"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{}}}}'
```

- The two `_meta` keys are required. `io.modelcontextprotocol/clientInfo` is optional.
- The headers must agree with the body. A different `Mcp-Name` gets HTTP 400 with JSON-RPC error `-32020`.
- A request with no token passes only with `MCP-Protocol-Version: 2026-07-28`, and only for `echo`, `tools/list`, or `server/discover`. Any other request with no token gets HTTP 401.
- A request with a valid token passes only with `MCP-Protocol-Version: 2026-07-28`, and only for `echo`, `secure_action`, `tools/list`, or `server/discover` (deny by default). Any other request with a valid token gets HTTP 403 `insufficient_scope` with `asap:capability_denied`.
- `secure_action` needs `Authorization: Bearer <token>`: an Agent JWT (`typ: agent+jwt`) or an access token from the test issuer. An Agent JWT is single-use and lives 60 seconds, so a client mints one per request; an access token works on every request until it expires.

## Compliance mode (test only)

> **Warning:** compliance mode hands out valid tokens to anyone who can reach the server. Use it only for a compliance run on a local machine or in CI. It is off by default, and only the command-line flag turns it on; no environment variable does.

```bash
pnpm --filter example-mcp-auth-http start -- --compliance
```

In this mode, each `POST /compliance/probe-tokens` returns a fresh set of probe tokens as JSON: `profile`, `test_only`, `mcp_url`, `protected_tool`, `allowed_arguments`, `constraint_violation_arguments`, `agent_jwt.valid`, `agent_jwt.out_of_grant`, `agent_jwt.constraint_violation`, `external.valid`, and `external.out_of_grant` (no `external` when the server runs with Agent JWTs only). Agent JWTs are single-use and live 60 seconds, so each one works for one call.

The `mcp-auth-http` profile of `asap-compliance` (`ComplianceEndpointTokenProvider`) posts once for each token that it needs. It requires `test_only: true` and reads only `agent_jwt.valid`, `agent_jwt.out_of_grant`, `external.valid`, and `external.out_of_grant`; it ignores the other fields, because its config holds the URL, the tool, and the arguments. Its constraint check sends a fresh `agent_jwt.valid` (or `external.valid`) token with the constraint-breaking arguments, so it never uses `agent_jwt.constraint_violation`. It builds the missing-token and invalid-token cases itself. `test/compliance.test.ts` uses every token of a set, `agent_jwt.constraint_violation` included, in two rounds with plain `fetch`.

## SDK clients

An SDK client must opt in to this revision with `versionNegotiation: { mode: { pin: "2026-07-28" } }` or `{ mode: "auto" }`. Its default mode sends the 2025 `initialize` handshake. The middleware refuses it with HTTP 401 when there is no token, or with HTTP 403 when the token verifies. To send a fresh Agent JWT on every request, give the transport a mint hook: `new StreamableHTTPClientTransport(url, { authProvider: { token: () => mintAgentJwt() } })`.
