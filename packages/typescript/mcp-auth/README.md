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

## Error codes

| Case | Result |
|------|--------|
| No token | HTTP 401, `asap:auth_required` |
| Invalid, expired, or wrong-audience token | HTTP 401, `asap:invalid_token` |
| Capability not granted | Tool result with `isError: true`, `asap:capability_denied` (HTTP 403 `insufficient_scope` on the external-token path) |
| Constraint violated | Tool result with `isError: true`, `asap:constraint_violation` |

## Links

- Python bridge: [docs/adapters/mcp-auth-bridge.md](https://github.com/asap-protocol/asap-protocol/blob/main/docs/adapters/mcp-auth-bridge.md)
