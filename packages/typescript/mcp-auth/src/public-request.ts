/**
 * The MCP protocol revision this package targets.
 *
 * The SDK does not export it: in `@modelcontextprotocol/server` 2.4.0,
 * `LATEST_PROTOCOL_VERSION` is still `"2025-11-25"`, and the 2026-07-28
 * revision is internal to the SDK.
 */
export const MCP_PROTOCOL_VERSION = "2026-07-28";

/** Methods that any request may call under the header rule, besides `tools/call` for a callable tool. */
const PUBLIC_MCP_METHODS: readonly string[] = ["tools/list", "server/discover"];
const NO_METHODS: ReadonlySet<string> = new Set();

/**
 * The header rule. It reads headers only, before the body is parsed.
 * It requires
 * `MCP-Protocol-Version: 2026-07-28`: the SDK checks `Mcp-Method` and
 * `Mcp-Name` against the body only on that revision, and a 2025-era request
 * with a forged `Mcp-Name` ran a protected tool (docs/adapters/mcp-auth-bridge.md).
 * `tools/call` passes only for a tool in `callableTools`; `tools/list`,
 * `server/discover`, and `extraMethods` pass; every other method is refused.
 * A repeated header arrives joined with ", " and so never matches, and the
 * base64 form of `Mcp-Name` never matches a tool name.
 */
export function isAllowedMcpRequest(headers: Headers, callableTools: ReadonlySet<string>, extraMethods: ReadonlySet<string>): boolean {
  if (headers.get("mcp-protocol-version") !== MCP_PROTOCOL_VERSION) {
    return false;
  }
  const method = headers.get("mcp-method");
  if (method === "tools/call") {
    const toolName = headers.get("mcp-name");
    return toolName !== null && callableTools.has(toolName);
  }
  return method !== null && (PUBLIC_MCP_METHODS.includes(method) || extraMethods.has(method));
}

/** The header rule for a request with no `Authorization` header: public tools, `tools/list`, and `server/discover` only. */
export function isPublicMcpRequest(headers: Headers, publicTools: ReadonlySet<string>): boolean {
  return isAllowedMcpRequest(headers, publicTools, NO_METHODS);
}
