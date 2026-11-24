# Example: HTTP MCP server protected by `@asap-protocol/mcp-auth`

A Streamable HTTP MCP server on `@modelcontextprotocol/server` 2.x (MCP revision `2026-07-28`), protected by `@asap-protocol/mcp-auth`. It has one public tool, `echo`, and one protected tool, `secure_action`.

At each start, the server creates a demo ASAP host and agent in memory. The agent has one grant: capability `secure_action`, with the constraint that `action` is not `forbidden-action`. With an ASAP Agent JWT, the package is a custom auth strategy, which MCP allows; it is not MCP authorization (OAuth 2.1).

The server serves only the `2026-07-28` revision (`legacy: "reject"`), and it accepts only local `Host` and `Origin` values.

## Smoke test

```bash
pnpm --filter example-mcp-auth-http test
```

`test/protected.test.ts` starts the server on a free local port and runs the five cases of MCP-TS-002 with the SDK client, pinned to `2026-07-28`:

| Case | Layer | Answer |
|------|-------|--------|
| Granted call | Tool result | `executed: <action>` |
| No token | HTTP | 401, `asap:auth_required` |
| Invalid token | HTTP | 401, `asap:invalid_token: <reason>` |
| Token without the capability | Tool result | `isError: true`, `asap:capability_denied` |
| `action: "forbidden-action"` | Tool result | `isError: true`, `asap:constraint_violation` |

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
- `secure_action` needs `Authorization: Bearer <Agent JWT>`. An Agent JWT is single-use and lives 60 seconds, so a client mints one per request.

An SDK client must opt in to this revision with `versionNegotiation: { mode: { pin: "2026-07-28" } }` or `{ mode: "auto" }`. Its default mode sends the 2025 `initialize` handshake, which this server refuses with `-32022`. To send a fresh Agent JWT on every request, give the transport a mint hook: `new StreamableHTTPClientTransport(url, { authProvider: { token: () => mintAgentJwt() } })`.
