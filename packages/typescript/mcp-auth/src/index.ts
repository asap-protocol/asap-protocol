/**
 * `@asap-protocol/mcp-auth`: ASAP identity and capability grants for HTTP MCP
 * servers built on `@modelcontextprotocol/server` 2.x (Streamable HTTP only).
 *
 * @example
 * ```ts
 * import { createMcpAuthMiddleware } from "@asap-protocol/mcp-auth";
 * const auth = createMcpAuthMiddleware({ agentJwt: { audience, resolveAgent }, publicTools: ["echo"] });
 * ```
 */

export { MCP_PROTOCOL_VERSION } from "./public-request.js";
export { createMcpAuthMiddleware } from "./middleware.js";
export type { McpAuthConfig, McpAuthMiddleware } from "./middleware.js";

export { McpAuthErrorCode, toolErrorResult } from "./errors.js";
export { createAgentJwtVerifier } from "./agent-jwt-verifier.js";
export type { AgentJwtPrincipal, AgentJwtVerifierConfig } from "./agent-jwt-verifier.js";

// MCP-TS-003: SDK types that appear in this package's public signatures.
export type { AuthInfo, CallToolResult, OAuthTokenVerifier } from "@modelcontextprotocol/server";
// The same for the @asap-protocol/client types in the verifier config.
export type { AgentKeyLookup, AgentKeyResolver, JtiReplayCache, ResolvedAgentKey } from "@asap-protocol/client";
