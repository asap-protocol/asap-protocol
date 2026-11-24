/**
 * `@asap-protocol/mcp-auth`: ASAP identity and capability grants for HTTP MCP
 * servers built on `@modelcontextprotocol/server` 2.x (Streamable HTTP only).
 *
 * @example
 * ```ts
 * import { MCP_PROTOCOL_VERSION } from "@asap-protocol/mcp-auth";
 * request.headers.get("mcp-protocol-version") === MCP_PROTOCOL_VERSION;
 * ```
 */

/**
 * The MCP protocol revision this package targets.
 *
 * The SDK does not export it: in `@modelcontextprotocol/server` 2.4.0,
 * `LATEST_PROTOCOL_VERSION` is still `"2025-11-25"`, and the 2026-07-28
 * revision is internal to the SDK.
 */
export const MCP_PROTOCOL_VERSION = "2026-07-28";

// MCP-TS-003: SDK types that appear in this package's public signatures.
export type { AuthInfo, OAuthTokenVerifier } from "@modelcontextprotocol/server";
