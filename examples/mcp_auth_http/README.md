# Example: HTTP MCP server for `@asap-protocol/mcp-auth`

A Streamable HTTP MCP server on `@modelcontextprotocol/server` 2.x (MCP revision `2026-07-28`). It has one public tool, `echo`, and one tool to protect, `secure_action`. The server has no auth yet: a later change adds `@asap-protocol/mcp-auth`.

The server serves only the `2026-07-28` revision (`legacy: "reject"`), and it accepts only local `Host` and `Origin` values.

## Run

```bash
pnpm install
pnpm --filter example-mcp-auth-http start   # MCP endpoint: http://127.0.0.1:8787/mcp
```

## Call a tool without an MCP SDK

The `2026-07-28` revision has no `initialize` handshake and no session. One POST calls a tool:

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
- A request with no `_meta` envelope (2025-era traffic) gets HTTP 400 with JSON-RPC error `-32022`.

An SDK client must opt in to this revision with `versionNegotiation: { mode: { pin: "2026-07-28" } }` or `{ mode: "auto" }`. Its default mode sends the 2025 `initialize` handshake, which this server refuses with `-32022`.

## Test

```bash
pnpm --filter example-mcp-auth-http test
```
