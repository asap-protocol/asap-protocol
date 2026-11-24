import type { CallToolResult } from "@modelcontextprotocol/server";

/**
 * The four ASAP error codes, with the same strings as Python
 * `asap.mcp.auth.errors` (MCP-TS-002). The token codes travel in an HTTP 401;
 * the grant codes travel in a tool result (README, "Error codes").
 *
 * Shared vector: `tests/fixtures/interop/mcp-auth-errors.json`.
 */
export const McpAuthErrorCode = {
  AuthRequired: "asap:auth_required",
  InvalidToken: "asap:invalid_token",
  CapabilityDenied: "asap:capability_denied",
  ConstraintViolation: "asap:constraint_violation",
} as const;

export type McpAuthErrorCode = (typeof McpAuthErrorCode)[keyof typeof McpAuthErrorCode];

/** The `asap:invalid_token` reason for a token that cannot be decoded, the same with either verifier and in every config. */
export const MALFORMED_TOKEN_REASON = "malformed token";

/**
 * Build a `tools/call` error result: one text item `"<code>"` or
 * `"<code>: <detail>"`, and `isError: true`. Same payload as Python
 * `tool_error_result`. The detail must never hold token content.
 *
 * @example
 * ```ts
 * return toolErrorResult(McpAuthErrorCode.CapabilityDenied, "no active grant for capability 'web_search'");
 * ```
 */
export function toolErrorResult(code: McpAuthErrorCode, detail?: string): CallToolResult {
  const text = detail === undefined ? code : `${code}: ${detail}`;
  return { content: [{ type: "text", text }], isError: true };
}
