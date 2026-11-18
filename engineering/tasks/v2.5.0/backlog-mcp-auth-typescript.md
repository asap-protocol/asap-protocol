# Backlog: `@asap-protocol/mcp-auth` (npm — v2.6.0 Interop)

> **Status**: MOVED (2026-11-18) — scope moved to [`product/prd/prd-interop.md`](../../../product/prd/prd-interop.md) (**v2.6.0**, **P1**, MCP-TS-001..003 now MUST). Execution tasks will live under `engineering/tasks/v2.6.0/` at kickoff; this file stays as the historical backlog. Originally deferred from v2.5.0 (S4 spike). Git tag **`v2.5.0.1`** was used for PyPI **`asap-compliance` 1.3.0** only.
> **PRD (current)**: [prd-interop.md §4.3](../../../product/prd/prd-interop.md#43-asap-protocolmcp-auth-for-http-mcp-servers-p1)
> **PRD (origin)**: [MCP-TS-001..003](../../../product/prd/prd-v2.5.0-mcp-auth-bridge.md#54-typescript-should)
> **Spike**: [typescript-mcp-auth-spike.md](./typescript-mcp-auth-spike.md)
> **Carry-over**: [PRD v2.5.3 §4](../../../product/prd/prd-v2.5.3-adapter-lab-ii.md#4-carry-over-from-v250-asap-protocolmcp-auth)

v2.5.0 shipped the **Python stdio** MCP Auth Bridge. HTTP TypeScript middleware was SHOULD-scope for a future npm release; it is now P1 in v2.6.0 Interop. Priorities in the table below are the original v2.5.0 values.

## Requirements (MCP-TS-*)

| ID | Task | Priority |
|----|------|----------|
| MCP-TS-001 | Publish `@asap-protocol/mcp-auth` with `createMcpAuthMiddleware(config)` for HTTP/SSE MCP servers | SHOULD |
| MCP-TS-002 | Bearer extraction + same `asap:*` error code mapping as Python | SHOULD |
| MCP-TS-003 | Re-export types compatible with `@modelcontextprotocol/sdk` | SHOULD |

## Implementation checklist

- [ ] Add `verifyAgentJwt` to `@asap-protocol/client` or `mcp-auth` (port `verify_agent_jwt` semantics)
- [ ] Scaffold `packages/typescript/mcp-auth` (mirror mastra/openai-agents layout)
- [ ] Implement `createMcpAuthMiddleware` (SDK HTTP transport + `tools/call` wrapper)
- [ ] vitest: Bearer extract, four error codes, success path
- [ ] Extend `publish-typescript.yml` for `v2.5.*` tags + fourth package publish
- [ ] `docs/integrations/mcp-auth-typescript.md` or extend adapter guide for HTTP/SSE

## References

- [CHANGELOG v2.5.0 TypeScript defer](../../../CHANGELOG.md#typescript)
- [Python adapter guide](../../../docs/adapters/mcp-auth-bridge.md)
