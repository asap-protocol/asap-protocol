import { createAgent, createHost, MemoryStorage, type AgentContext } from "@asap-protocol/client";
import { createMcpHandler, McpServer, type ServerContext } from "@modelcontextprotocol/server";
import { beforeAll, beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { createMcpAuthMiddleware, staticAgentGrants, type McpAuthMiddleware, type McpFetchHandler } from "../src/index.js";
import { MCP_ENDPOINT, post, PROTECTED_TOOL, toolsCallBody, toolsCallHeaders } from "./mcp-requests.js";

/**
 * The middleware reports a result from a declared tool if the call did not
 * use the wrapper for that tool.
 */
const UNREGISTERED_TOOL = "listed_but_not_registered";

let agent: AgentContext;
let onInternalError: Mock<(error: unknown) => void>;
let toolRuns: string[];

beforeAll(async () => {
  agent = await createAgent(await createHost({ storage: new MemoryStorage() }), { mode: "delegated" });
});

beforeEach(() => {
  onInternalError = vi.fn<(error: unknown) => void>();
  toolRuns = [];
});

function middleware(): McpAuthMiddleware {
  return createMcpAuthMiddleware({
    agentJwt: { audience: MCP_ENDPOINT, resolveAgent: async ({ agentId }) => (agentId === agent.agentId ? { publicKey: agent.publicJwk, usable: true } : undefined) },
    toolCapabilities: { [PROTECTED_TOOL]: PROTECTED_TOOL, [UNREGISTERED_TOOL]: PROTECTED_TOOL },
    agentGrants: staticAgentGrants({ [agent.agentId]: [{ capability: PROTECTED_TOOL }] }),
    onInternalError,
  });
}

async function runTool(_ctx: ServerContext) {
  toolRuns.push(PROTECTED_TOOL);
  return { content: [{ type: "text" as const, text: "ran" }] };
}

type Register = (server: McpServer, auth: McpAuthMiddleware) => void;

/** `responseMode: "sse"` makes the SDK answer with an event stream, as it does when a tool sends a notification first. */
async function call(tool: string, capabilities: string[], register: Register, handler?: McpFetchHandler, responseMode?: "sse") {
  const auth = middleware();
  const mcp = handler ?? createMcpHandler(() => {
    const server = new McpServer({ name: "mcp-auth-unwrapped-test", version: "0.0.0" });
    register(server, auth);
    return server;
  }, { legacy: "reject", responseMode });
  const token = await agent.signAgentJwt({ aud: MCP_ENDPOINT, capabilities });
  return auth.protect(mcp).fetch(post([...toolsCallHeaders(tool), ["authorization", `Bearer ${token}`]], toolsCallBody(tool)));
}

describe("a tool in toolCapabilities registered without protectTool", () => {
  it.each<[string, "sse" | undefined, string]>([
    ["a JSON result", undefined, "application/json"],
    ["an event stream", "sse", "text/event-stream"],
  ])("is reported to onInternalError after the call, with %s", async (_case, responseMode, contentType) => {
    const response = await call(PROTECTED_TOOL, [], (server) => server.registerTool(PROTECTED_TOOL, {}, runTool), undefined, responseMode);
    await response.text();

    expect(response.headers.get("content-type")).toContain(contentType);
    // The token carries no capability: a wrapped tool would have refused the call.
    expect(toolRuns).toEqual([PROTECTED_TOOL]);
    expect(onInternalError).toHaveBeenCalledOnce();
    expect(String(onInternalError.mock.calls[0]![0])).toContain(`The call to "${PROTECTED_TOOL}" returned a result without its protectTool wrapper`);
  });

  it.each<[string, string[], "sse"?]>([
    ["a granted call", [PROTECTED_TOOL]],
    ["a denied call", []],
    ["a granted streaming call", [PROTECTED_TOOL], "sse"],
  ])("is not reported when the tool is wrapped: %s", async (_case, capabilities, responseMode) => {
    const response = await call(PROTECTED_TOOL, capabilities, (server, auth) => server.registerTool(PROTECTED_TOOL, {}, auth.protectTool(PROTECTED_TOOL, runTool)), undefined, responseMode);

    expect(response.status).toBe(200);
    await response.text();
    expect(onInternalError).not.toHaveBeenCalled();
  });

  it("checks the callback on each request served by the factory", async () => {
    const auth = middleware();
    const wrappedTool = auth.protectTool(PROTECTED_TOOL, runTool);
    const app = auth.protect(createMcpHandler(({ requestInfo }) => {
      const server = new McpServer({ name: "mcp-auth-unwrapped-test", version: "0.0.0" });
      const wrapped = requestInfo?.headers.get("x-tool-variant") === "wrapped";
      server.registerTool(PROTECTED_TOOL, {}, wrapped ? wrappedTool : runTool);
      return server;
    }, { legacy: "reject" }));

    for (const variant of ["wrapped", "unwrapped"]) {
      const token = await agent.signAgentJwt({ aud: MCP_ENDPOINT, capabilities: [PROTECTED_TOOL] });
      const headers: [string, string][] = [...toolsCallHeaders(PROTECTED_TOOL), ["authorization", `Bearer ${token}`], ["x-tool-variant", variant]];
      const response = await app.fetch(post(headers, toolsCallBody(PROTECTED_TOOL)));
      expect(response.status).toBe(200);
      await response.text();
    }

    expect(onInternalError).toHaveBeenCalledOnce();
  });

  it("is not reported for a listed tool that the server does not register (a JSON-RPC error, no tool ran)", async () => {
    const response = await call(UNREGISTERED_TOOL, [PROTECTED_TOOL], () => undefined);

    expect(((await response.json()) as { error?: unknown }).error).toBeDefined();
    expect(onInternalError).not.toHaveBeenCalled();
  });

  it.each<[string, Response]>([
    ["an answer other than 200", new Response(null, { status: 400 })],
    ["a 200 body that is not JSON", new Response("not json", { status: 200, headers: { "content-type": "application/json" } })],
    ["a 200 JSON body that is not a JSON-RPC object", Response.json([])],
  ])("is not reported for %s", async (_case, answer) => {
    await call(PROTECTED_TOOL, [], () => undefined, { fetch: async () => answer });

    expect(onInternalError).not.toHaveBeenCalled();
  });
});
