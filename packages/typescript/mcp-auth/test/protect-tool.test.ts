import { readFileSync } from "node:fs";

import type { ServerContext } from "@modelcontextprotocol/server";
import { describe, expect, it, vi } from "vitest";

import {
  createMcpAuthMiddleware,
  staticAgentGrants,
  toolErrorResult,
  type AgentJwtPrincipal,
  type AuthInfo,
  type CallToolResult,
  type CapabilityGrant,
  type McpAuthConfig,
} from "../src/index.js";
import { MCP_ENDPOINT, PUBLIC_TOOL } from "./mcp-requests.js";

const AGENT_ID = "agent_01";
const TOOL = "search";
const CAPABILITY = "web_search";
const OK: CallToolResult = { content: [{ type: "text", text: "ran" }] };

// Shared with Python asap.mcp.auth.errors: the grant layer's payloads must equal these.
const VECTOR_URL = new URL("../../../../tests/fixtures/interop/mcp-auth-errors.json", import.meta.url);
const errorVector = JSON.parse(readFileSync(VECTOR_URL, "utf8")) as { tool_error_results: { code: string; result: unknown }[] };
const vectorResult = (code: string) => errorVector.tool_error_results.find((entry) => entry.code === code)?.result;

function agentAuthInfo(capabilities: readonly string[] = [CAPABILITY]): AuthInfo {
  const principal: AgentJwtPrincipal = { kind: "agent_jwt", agentId: AGENT_ID, hostThumbprint: "host_tp", capabilities };
  return { token: "unused", clientId: AGENT_ID, scopes: [], expiresAt: 4_102_444_800, extra: { principal } };
}

function ctxWith(authInfo: AuthInfo | undefined): ServerContext {
  return { http: authInfo === undefined ? undefined : { authInfo } } as unknown as ServerContext;
}

function middleware(grants: readonly CapabilityGrant[] | undefined, overrides: Partial<McpAuthConfig> = {}) {
  return createMcpAuthMiddleware({
    agentJwt: { audience: MCP_ENDPOINT, resolveAgent: async () => undefined },
    publicTools: [PUBLIC_TOOL],
    toolCapabilities: { [TOOL]: CAPABILITY },
    ...(grants === undefined ? {} : { agentGrants: staticAgentGrants({ [AGENT_ID]: grants }) }),
    ...overrides,
  });
}

/** Calls the wrapped tool the way the SDK does for a tool with an input schema: (args, ctx). */
async function callTool(auth: ReturnType<typeof middleware>, authInfo: AuthInfo | undefined, args: Record<string, unknown> = {}) {
  const handler = vi.fn(async (_args: Record<string, unknown>, _ctx: ServerContext) => OK);
  const result = await auth.protectTool(TOOL, handler)(args, ctxWith(authInfo));
  return { result, handler };
}

describe("protectTool: the grant layer for Agent JWT principals (tool result)", () => {
  it("runs the handler with the SDK's arguments for a granted call that meets the constraints", async () => {
    const auth = middleware([{ capability: CAPABILITY, constraints: { q: { in: ["x", "y"] } } }]);

    const { result, handler } = await callTool(auth, agentAuthInfo(), { q: "x" });

    expect(result).toEqual(OK);
    expect(handler).toHaveBeenCalledWith({ q: "x" }, ctxWith(agentAuthInfo()));
  });

  it("runs a tool with no input schema, which the SDK calls with ctx only", async () => {
    const handler = vi.fn(async (_ctx: ServerContext) => OK);
    const ctx = ctxWith(agentAuthInfo());

    expect(await middleware([{ capability: CAPABILITY }]).protectTool(TOOL, handler)(ctx)).toEqual(OK);
    expect(handler).toHaveBeenCalledWith(ctx);
  });

  it("denies a capability that the token's capabilities claim does not hold, as Python does", async () => {
    const { result, handler } = await callTool(middleware([{ capability: CAPABILITY }]), agentAuthInfo(["other"]));

    expect(result).toEqual(toolErrorResult("asap:capability_denied", "JWT capabilities claim does not include 'web_search'"));
    expect(handler).not.toHaveBeenCalled();
  });

  it.each<[string, Partial<McpAuthConfig>]>([
    ["a grant for another capability", { agentGrants: staticAgentGrants({ [AGENT_ID]: [{ capability: "other" }] }) }],
    ["an agent that the static map does not list", { agentGrants: staticAgentGrants({ agent_02: [{ capability: CAPABILITY }] }) }],
    ["no agentGrants config (deny by default)", {}],
  ])("denies a call with %s, with the shared vector payload", async (_case, overrides) => {
    const { result, handler } = await callTool(middleware(undefined, overrides), agentAuthInfo());

    expect(result).toEqual(vectorResult("asap:capability_denied"));
    expect(handler).not.toHaveBeenCalled();
  });

  it("denies a call that breaks a grant constraint, and names the field", async () => {
    const auth = middleware([{ capability: CAPABILITY, constraints: { max_results: { max: 10 } } }]);

    const { result, handler } = await callTool(auth, agentAuthInfo(), { max_results: 50 });

    expect(result).toEqual(toolErrorResult("asap:constraint_violation", "max_results: 50 exceeds maximum 10"));
    expect(handler).not.toHaveBeenCalled();
  });

  it("fails closed on a constrained grant when the tool gets no arguments (no input schema)", async () => {
    const auth = middleware([{ capability: CAPABILITY, constraints: { env: "dev" } }]);

    const result = await auth.protectTool(TOOL, async (_ctx: ServerContext) => OK)(ctxWith(agentAuthInfo()));

    expect(result).toEqual(toolErrorResult("asap:constraint_violation", "env: missing required argument"));
  });

  it("fails closed on grant constraints that are not an object", async () => {
    const auth = middleware([{ capability: CAPABILITY, constraints: "env=dev" as unknown as Record<string, unknown> }]);

    const { result, handler } = await callTool(auth, agentAuthInfo());

    expect(result).toEqual(toolErrorResult("asap:constraint_violation", "malformed grant constraints"));
    expect(handler).not.toHaveBeenCalled();
  });

  it("uses the tool name as the capability when the map does not list the tool", async () => {
    const auth = middleware([{ capability: "echo_upper" }]);

    expect(await auth.protectTool("echo_upper", async (_ctx: ServerContext) => OK)(ctxWith(agentAuthInfo(["echo_upper"])))).toEqual(OK);
  });
});

describe("protectTool: fail closed", () => {
  it.each<[string, AuthInfo | undefined]>([
    ["no auth info", undefined],
    ["auth info with no ASAP principal", { token: "t", clientId: "c", scopes: [] }],
    ["a principal of an unknown kind", { token: "t", clientId: "c", scopes: [], extra: { principal: { kind: "other" } } }],
  ])("refuses a protected tool with %s, with the shared vector payload", async (_case, authInfo) => {
    const { result, handler } = await callTool(middleware([{ capability: CAPABILITY }]), authInfo);

    expect(result).toEqual(vectorResult("asap:auth_required"));
    expect(handler).not.toHaveBeenCalled();
  });

  it("denies the call when the grant resolver throws, with a fixed detail, and reports the error", async () => {
    const onInternalError = vi.fn();
    const auth = middleware(undefined, { onInternalError, agentGrants: async () => Promise.reject(new Error("db.internal:5432 refused")) });

    const { result, handler } = await callTool(auth, agentAuthInfo());

    expect(result).toEqual(toolErrorResult("asap:capability_denied", "grant lookup failed"));
    expect(handler).not.toHaveBeenCalled();
    expect(onInternalError).toHaveBeenCalledOnce();
  });

  it("runs a public tool with no auth info and no grant check", async () => {
    const handler = vi.fn(async (_ctx: ServerContext) => OK);

    expect(await middleware(undefined).protectTool(PUBLIC_TOOL, handler)(ctxWith(undefined))).toEqual(OK);
  });
});
