import type { AuthInfo, CallToolResult, ScopeChallenge, ScopeChallengeHandler, ServerContext, ToolCallback } from "@modelcontextprotocol/server";

import type { AgentJwtPrincipal } from "./agent-jwt-verifier.js";
import type { CapabilityGrant } from "./capability-grants.js";
import { isJsonObject, validateConstraints } from "./constraints.js";
import { McpAuthErrorCode, toolErrorResult } from "./errors.js";
import type { ExternalIssuerConfig } from "./external-issuers.js";
import type { ExternalPrincipal } from "./external-token-verifier.js";

/** The ASAP principal in `AuthInfo.extra.principal`, by token type. */
export type McpAuthPrincipal = AgentJwtPrincipal | ExternalPrincipal;

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
  /** Tool name → ASAP capability, for every protected tool. `protectTool` accepts no other name. */
  readonly toolCapabilities?: Readonly<Record<string, string>>;
  readonly agentGrants?: AgentGrantResolver;
  /** The external issuers, for the scope that a 403 challenge asks for. */
  readonly externalIssuers?: readonly ExternalIssuerConfig[];
  readonly reportInternalError: (error: unknown) => void;
}

export interface GrantLayer {
  readonly protectTool: ProtectTool;
  readonly scopeChallenge: ScopeChallengeHandler;
  /** Whether this call entered the wrapper for its tool. */
  readonly usedWrapper: (authInfo: AuthInfo | undefined) => boolean;
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

function principalOf(authInfo: AuthInfo | undefined): McpAuthPrincipal | undefined {
  const principal = authInfo?.extra?.principal;
  const known = isJsonObject(principal) && (principal.kind === "agent_jwt" || principal.kind === "external");
  return known ? (principal as unknown as McpAuthPrincipal) : undefined;
}

/** Per issuer, the first scope rule for each capability: the scope that a 403 challenge asks for. */
function challengeScopes(issuers: readonly ExternalIssuerConfig[]): ReadonlyMap<string, ReadonlyMap<string, string>> {
  const byIssuer = new Map<string, Map<string, string>>();
  for (const issuer of issuers) {
    const byCapability = new Map<string, string>();
    for (const rule of issuer.grantRules ?? []) {
      if (typeof rule.scope === "string" && !byCapability.has(rule.capability)) byCapability.set(rule.capability, rule.scope);
    }
    byIssuer.set(issuer.issuer, byCapability);
  }
  return byIssuer;
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

function wrapError(name: string, problem: string): TypeError {
  return new TypeError(`protectTool(${JSON.stringify(name)}): ${problem}`);
}

/**
 * The grant layer (MCP-TS-002). `protectTool` wraps a tool callback; a
 * protected tool with no verified principal fails closed with
 * `asap:auth_required`, the second guard behind the token layer's header
 * rule. The checks and texts follow Python `ProtectedMCPServer`.
 * `protectTool` is bound to its name: the name must be
 * in `toolCapabilities`, and a call whose `Mcp-Name` is another tool fails.
 * `scopeChallenge` is the external-token exception: a
 * missing grant that a scope rule could give is HTTP 403 `insufficient_scope`
 * before the tool runs; a missing grant that no scope gives stays a tool result.
 */
export function createGrantLayer(config: GrantLayerConfig): GrantLayer {
  const toolCapabilities = new Map(Object.entries(config.toolCapabilities ?? {}));
  const agentGrants = config.agentGrants ?? (() => []);
  const scopesByIssuer = challengeScopes(config.externalIssuers ?? []);
  // Each verified request gets its own AuthInfo. The SDK passes it to the callback.
  const callsUsingWrapper = new WeakSet<AuthInfo>();

  async function agentGrantsOf(principal: AgentJwtPrincipal): Promise<readonly CapabilityGrant[] | undefined> {
    try {
      return await agentGrants(principal);
    } catch (error) {
      config.reportInternalError(error);
      return undefined;
    }
  }

  /**
   * With `scopeChallenge` on the tool, the SDK answers this case with HTTP 403
   * before the tool runs. So when it reaches the grant layer, the tool was registered without it.
   */
  function reportMissingScopeChallenge(name: string, capability: string, principal: McpAuthPrincipal): void {
    const scope = principal.kind === "external" ? scopesByIssuer.get(principal.issuer)?.get(capability) : undefined;
    if (scope === undefined) return;
    config.reportInternalError(
      new Error(
        `protectTool(${JSON.stringify(name)}): pass scopeChallenge: auth.scopeChallenge to registerTool for this tool; ` +
          `an access token without the scope ${JSON.stringify(scope)} got a tool result instead of HTTP 403 insufficient_scope`,
      ),
    );
  }

  async function grantDenial(name: string, capability: string, authInfo: AuthInfo | undefined, args: unknown): Promise<CallToolResult | undefined> {
    const principal = principalOf(authInfo);
    if (principal === undefined) return toolErrorResult(McpAuthErrorCode.AuthRequired);
    if (principal.kind === "agent_jwt" && !(Array.isArray(principal.capabilities) && principal.capabilities.includes(capability))) {
      return denied(`JWT capabilities claim does not include '${capability}'`);
    }
    const grants = principal.kind === "external" ? principal.grants : await agentGrantsOf(principal);
    if (grants === undefined) return denied("grant lookup failed");
    const grant = grants.find((candidate) => candidate.capability === capability);
    if (grant !== undefined) return constraintDenial(grant, args);
    reportMissingScopeChallenge(name, capability, principal);
    return denied(`no active grant for capability '${capability}'`);
  }

  function bindingDenial(name: string): CallToolResult {
    config.reportInternalError(new Error(`protectTool(${JSON.stringify(name)}) ran for a request whose Mcp-Name is not that tool; register the callback under the same name`));
    return denied(`the request is not a call to '${name}'`);
  }

  function protectTool(name: string, callback: (...params: unknown[]) => ReturnType<ToolCallback>) {
    if (config.publicTools.has(name)) throw wrapError(name, "the tool is in publicTools; register a public tool without protectTool");
    const capability = toolCapabilities.get(name);
    if (capability === undefined) throw wrapError(name, "the tool is not in toolCapabilities; list every protected tool there");
    // The SDK calls (args, ctx) for a tool with an inputSchema and (ctx) for one without.
    return async (...params: unknown[]) => {
      const ctx = params.at(-1) as ServerContext | undefined;
      const http = ctx?.http;
      // The SDK checks Mcp-Name against the body on a 2026-07-28 request.
      if (http?.req?.headers.get("mcp-name") !== name) return bindingDenial(name);
      const authInfo = http.authInfo;
      if (authInfo !== undefined) callsUsingWrapper.add(authInfo);
      const denial = await grantDenial(name, capability, authInfo, params.length > 1 ? params[0] : undefined);
      return denial ?? callback(...params);
    };
  }

  function scopeChallenge({ request, authInfo }: Parameters<ScopeChallengeHandler>[0]): ScopeChallenge | undefined {
    const tool = request.method === "tools/call" ? request.params?.name : undefined;
    const principal = principalOf(authInfo);
    const capability = typeof tool === "string" ? toolCapabilities.get(tool) : undefined;
    if (capability === undefined || principal?.kind !== "external") return undefined;
    if (principal.grants.some((grant) => grant.capability === capability)) return undefined;
    const scope = scopesByIssuer.get(principal.issuer)?.get(capability);
    return scope === undefined ? undefined : { scopes: [scope], errorDescription: McpAuthErrorCode.CapabilityDenied };
  }

  return { protectTool: protectTool as ProtectTool, scopeChallenge, usedWrapper: (authInfo) => authInfo !== undefined && callsUsingWrapper.has(authInfo) };
}
