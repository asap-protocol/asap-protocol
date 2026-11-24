import type { AuthInfo, CallToolResult, ServerContext, ToolCallback } from "@modelcontextprotocol/server";

import type { AgentJwtPrincipal } from "./agent-jwt-verifier.js";
import type { CapabilityGrant } from "./capability-grants.js";
import { isJsonObject, validateConstraints } from "./constraints.js";
import { McpAuthErrorCode, toolErrorResult } from "./errors.js";

/** Resolves a verified Agent JWT principal to its grants. A throw denies the call with a fixed detail. */
export type AgentGrantResolver = (principal: AgentJwtPrincipal) => readonly CapabilityGrant[] | Promise<readonly CapabilityGrant[]>;

/** A tool callback with arguments, as `registerTool` passes them when the tool has an `inputSchema`. */
export type ToolCallbackWithArgs = (args: never, ctx: ServerContext) => ReturnType<ToolCallback>;

/**
 * `protectTool(name, callback)` returns a callback of the same type. Inline in
 * `registerTool`, the first signature takes the argument types from the
 * tool's `inputSchema`; the second takes them from an already typed callback;
 * the third is for a tool with no `inputSchema`, which the SDK calls with
 * `ctx` only. Write that callback with its `ctx` parameter.
 */
export interface ProtectTool {
  <Callback extends ToolCallbackWithArgs = never>(name: string, callback: NoInfer<Callback>): Callback;
  <Callback extends ToolCallbackWithArgs>(name: string, callback: Callback): Callback;
  (name: string, callback: ToolCallback): ToolCallback;
}

export interface GrantLayerConfig {
  readonly publicTools: ReadonlySet<string>;
  /** Tool name → ASAP capability. A tool that is not listed uses its own name (Python `resolve_capability`). */
  readonly toolCapabilities?: Readonly<Record<string, string>>;
  readonly agentGrants?: AgentGrantResolver;
  readonly reportInternalError: (error: unknown) => void;
}

/**
 * A grant resolver over a fixed map of agent id → grants. An agent that is not
 * in the map has no grants.
 *
 * @example
 * ```ts
 * createMcpAuthMiddleware({ agentJwt, agentGrants: staticAgentGrants({ agent_01: [{ capability: "web_search" }] }) });
 * ```
 */
export function staticAgentGrants(grantsByAgentId: Readonly<Record<string, readonly CapabilityGrant[]>>): AgentGrantResolver {
  const grants = new Map(Object.entries(grantsByAgentId));
  return (principal) => grants.get(principal.agentId) ?? [];
}

function agentPrincipalOf(authInfo: AuthInfo | undefined): AgentJwtPrincipal | undefined {
  const principal = authInfo?.extra?.principal;
  return isJsonObject(principal) && principal.kind === "agent_jwt" ? (principal as unknown as AgentJwtPrincipal) : undefined;
}

function denied(detail: string): CallToolResult {
  return toolErrorResult(McpAuthErrorCode.CapabilityDenied, detail);
}

/** Python `check_grant` constraints step, on the arguments the SDK passes to the tool. */
function constraintDenial(grant: CapabilityGrant, args: unknown): CallToolResult | undefined {
  const { constraints } = grant;
  if (constraints === undefined || constraints === null) return undefined;
  if (!isJsonObject(constraints)) return toolErrorResult(McpAuthErrorCode.ConstraintViolation, "malformed grant constraints");
  const violations = validateConstraints(constraints, isJsonObject(args) ? args : {});
  if (violations.length === 0) return undefined;
  return toolErrorResult(McpAuthErrorCode.ConstraintViolation, violations.map((violation) => violation.message).join("; "));
}

/**
 * The grant layer (MCP-TS-002): `protectTool` wraps a tool callback. A
 * protected tool with no verified principal fails closed with
 * `asap:auth_required`; that is the second guard behind the token layer's
 * header rule. The checks and texts follow Python `ProtectedMCPServer`.
 */
export function createProtectTool(config: GrantLayerConfig): ProtectTool {
  const toolCapabilities = new Map(Object.entries(config.toolCapabilities ?? {}));
  const agentGrants = config.agentGrants ?? (() => []);

  async function agentGrantsOf(principal: AgentJwtPrincipal): Promise<readonly CapabilityGrant[] | undefined> {
    try {
      return await agentGrants(principal);
    } catch (error) {
      config.reportInternalError(error);
      return undefined;
    }
  }

  async function grantDenial(capability: string, authInfo: AuthInfo | undefined, args: unknown): Promise<CallToolResult | undefined> {
    const principal = agentPrincipalOf(authInfo);
    if (principal === undefined) return toolErrorResult(McpAuthErrorCode.AuthRequired);
    if (!Array.isArray(principal.capabilities) || !principal.capabilities.includes(capability)) {
      return denied(`JWT capabilities claim does not include '${capability}'`);
    }
    const grants = await agentGrantsOf(principal);
    if (grants === undefined) return denied("grant lookup failed");
    const grant = grants.find((candidate) => candidate.capability === capability);
    return grant === undefined ? denied(`no active grant for capability '${capability}'`) : constraintDenial(grant, args);
  }

  function protectTool(name: string, callback: (...params: unknown[]) => ReturnType<ToolCallback>) {
    if (config.publicTools.has(name)) return callback;
    const capability = toolCapabilities.get(name) ?? name;
    // The SDK calls (args, ctx) for a tool with an inputSchema and (ctx) for one without.
    return async (...params: unknown[]) => {
      const ctx = params.at(-1) as ServerContext | undefined;
      const denial = await grantDenial(capability, ctx?.http?.authInfo, params.length > 1 ? params[0] : undefined);
      return denial ?? callback(...params);
    };
  }

  return protectTool as ProtectTool;
}
