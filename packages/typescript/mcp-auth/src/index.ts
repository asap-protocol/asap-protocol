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

export { MCP_PROTOCOL_VERSION } from "./public-request.js";

export { McpAuthErrorCode, toolErrorResult } from "./errors.js";
export { createAgentJwtVerifier } from "./agent-jwt-verifier.js";
export type { AgentJwtPrincipal, AgentJwtVerifierConfig } from "./agent-jwt-verifier.js";

// MCP-TS-003: SDK types that appear in this package's public signatures.
export type { AuthInfo, CallToolResult, OAuthTokenVerifier } from "@modelcontextprotocol/server";
// The same for the @asap-protocol/client types in the verifier config.
export type { AgentKeyLookup, AgentKeyResolver, JtiReplayCache, ResolvedAgentKey } from "@asap-protocol/client";
