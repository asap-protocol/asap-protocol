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
export type { McpAuthConfig, McpAuthMiddleware, McpFetchHandler, McpProtectedHandler } from "./middleware.js";

export { McpAuthErrorCode, toolErrorResult } from "./errors.js";
export { createAgentJwtVerifier } from "./agent-jwt-verifier.js";
export type { AgentJwtPrincipal, AgentJwtVerifierConfig } from "./agent-jwt-verifier.js";
export { validateConstraints } from "./constraints.js";
export type { ConstraintSet } from "./constraints.js";
export { assertValidGrantRules, mapExternalGrants } from "./capability-grants.js";
export type { CapabilityGrant, ExternalGrantRule } from "./capability-grants.js";

// MCP-TS-003: SDK types that appear in this package's public signatures.
export type { AuthInfo, CallToolResult, McpHandlerRequestOptions, OAuthTokenVerifier } from "@modelcontextprotocol/server";
// The same for the @asap-protocol/client types in the verifier config and the constraint check.
export type { AgentKeyLookup, AgentKeyResolver, ConstraintViolation, JtiReplayCache, ResolvedAgentKey } from "@asap-protocol/client";
