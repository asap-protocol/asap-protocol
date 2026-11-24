import { createAgent, createHost, MemoryStorage, type AgentContext } from "@asap-protocol/client";
import { createMcpHandler, McpServer, type ServerContext } from "@modelcontextprotocol/server";
import { beforeAll, describe, expect, it, vi } from "vitest";

import { createMcpAuthMiddleware, staticAgentGrants, toolErrorResult, type McpAuthMiddleware } from "../src/index.js";
import { MCP_ENDPOINT, post, PROTECTED_TOOL, PUBLIC_TOOL, toolsCallBody, toolsCallHeaders } from "./mcp-requests.js";

// Before this check, protectTool(name, cb) trusted the name the caller typed.
const DANGEROUS_TOOL = "delete_all";
const CAPABILITY = "secure_action";

let agent: AgentContext;

beforeAll(async () => {
  agent = await createAgent(await createHost({ storage: new MemoryStorage() }), { mode: "delegated" });
});

function middleware(onInternalError = vi.fn()): McpAuthMiddleware {
  return createMcpAuthMiddleware({
    agentJwt: { audience: MCP_ENDPOINT, resolveAgent: async ({ agentId }) => (agentId === agent.agentId ? { publicKey: agent.publicJwk, usable: true } : undefined) },
    publicTools: [PUBLIC_TOOL],
    toolCapabilities: { [PROTECTED_TOOL]: CAPABILITY, [DANGEROUS_TOOL]: "admin" },
    agentGrants: staticAgentGrants({ [agent.agentId]: [{ capability: CAPABILITY }] }),
    onInternalError,
  });
}

const callback = async (_ctx: ServerContext) => ({ content: [{ type: "text" as const, text: "ran" }] });

describe("protectTool checks the name it is given at wrap time", () => {
  it("throws for a public tool, so a protected callback cannot be left unwrapped by its name", () => {
    expect(() => middleware().protectTool(PUBLIC_TOOL, callback)).toThrow(`protectTool("${PUBLIC_TOOL}"): the tool is in publicTools`);
  });

  it("throws for a tool that toolCapabilities does not list", () => {
    expect(() => middleware().protectTool("unlisted", callback)).toThrow('protectTool("unlisted"): the tool is not in toolCapabilities');
  });
});

describe("protectTool refuses a call to another tool than the one it wraps", () => {
  it("does not run a callback registered under another protected tool's name", async () => {
    const onInternalError = vi.fn();
    const auth = middleware(onInternalError);
    const run = vi.fn(callback);
    const buildServer = () => {
      const server = new McpServer({ name: "mcp-auth-binding-test", version: "0.0.0" });
      // The misuse: the dangerous tool is wrapped under the name of a tool the agent holds a grant for.
      server.registerTool(DANGEROUS_TOOL, { description: "dangerous" }, auth.protectTool(PROTECTED_TOOL, run));
      return server;
    };
    const token = await agent.signAgentJwt({ aud: MCP_ENDPOINT, capabilities: [CAPABILITY] });
    const request = post([...toolsCallHeaders(DANGEROUS_TOOL), ["authorization", `Bearer ${token}`]], toolsCallBody(DANGEROUS_TOOL));

    const response = await auth.protect(createMcpHandler(buildServer, { legacy: "reject" })).fetch(request);

    expect(response.status).toBe(200);
    expect(((await response.json()) as { result: unknown }).result).toMatchObject(
      toolErrorResult("asap:capability_denied", `the request is not a call to '${PROTECTED_TOOL}'`),
    );
    expect(run).not.toHaveBeenCalled();
    // The binding error, then the report that protectTool never wrapped the dangerous tool under its own name.
    expect(onInternalError.mock.calls.map(([error]) => String(error))).toEqual([
      expect.stringContaining(`protectTool("${PROTECTED_TOOL}") ran for a request whose Mcp-Name is not that tool`),
      expect.stringContaining(`toolCapabilities lists "${DANGEROUS_TOOL}", but protectTool("${DANGEROUS_TOOL}", ...) was never called`),
    ]);
  });
});
