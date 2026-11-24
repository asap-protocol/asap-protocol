import { createAgent, createHost, MemoryStorage, type AgentContext } from "@asap-protocol/client";
import { createMcpHandler, McpServer } from "@modelcontextprotocol/server";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { createMcpAuthMiddleware, type McpFetchHandler } from "../src/index.js";
import { MCP_ENDPOINT, NOT_PUBLIC_REQUESTS, post, PROTECTED_TOOL, PUBLIC_REQUESTS, PUBLIC_TOOL, toolsCallBody, toolsCallHeaders } from "./mcp-requests.js";

let agent: AgentContext;
let toolRuns: string[];

beforeAll(async () => {
  agent = await createAgent(await createHost({ storage: new MemoryStorage() }), { mode: "delegated" });
});

beforeEach(() => {
  toolRuns = [];
});

/** A real SDK server: the protected tool answers with the agent id it reads from ctx.http.authInfo. */
function buildServer(): McpServer {
  const server = new McpServer({ name: "mcp-auth-protect-test", version: "0.0.0" });
  server.registerTool(PUBLIC_TOOL, { description: "public" }, async () => {
    toolRuns.push(PUBLIC_TOOL);
    return { content: [{ type: "text", text: "public" }] };
  });
  server.registerTool(PROTECTED_TOOL, { description: "protected" }, async (ctx) => {
    toolRuns.push(PROTECTED_TOOL);
    return { content: [{ type: "text", text: ctx.http?.authInfo?.clientId ?? "no auth info" }] };
  });
  return server;
}

// The SDK default (legacy: "stateless") is the weaker mode, so the bypass test runs against it.
function protectedHandler() {
  return protectedHandlerOf(createMcpHandler(buildServer));
}

function protectedHandlerOf(handler: McpFetchHandler) {
  const auth = createMcpAuthMiddleware({
    publicTools: [PUBLIC_TOOL],
    toolCapabilities: { [PROTECTED_TOOL]: PROTECTED_TOOL },
    agentJwt: {
      audience: MCP_ENDPOINT,
      resolveAgent: async ({ agentId }) => (agentId === agent.agentId ? { publicKey: agent.publicJwk, usable: true } : undefined),
    },
  });
  return auth.protect(handler);
}

async function resultText(response: Response): Promise<string> {
  const body = (await response.json()) as { result: { content: { text: string }[] } };
  return body.result.content[0]!.text;
}

describe("protect(handler) in front of the SDK's createMcpHandler", () => {
  it("passes the verified AuthInfo to the protected tool", async () => {
    const token = await agent.signAgentJwt({ aud: MCP_ENDPOINT });
    const request = post([...toolsCallHeaders(PROTECTED_TOOL), ["authorization", `Bearer ${token}`]], toolsCallBody(PROTECTED_TOOL));

    const response = await protectedHandler().fetch(request);

    expect(response.status).toBe(200);
    expect(await resultText(response)).toBe(agent.agentId);
    expect(toolRuns).toEqual([PROTECTED_TOOL]);
  });

  it("runs a public tool with no token and no auth info", async () => {
    const response = await protectedHandler().fetch(post(toolsCallHeaders(PUBLIC_TOOL), toolsCallBody(PUBLIC_TOOL)));

    expect(response.status).toBe(200);
    expect(await resultText(response)).toBe("public");
  });

  it("answers 401 before the SDK runs any tool, for every non-public request with no token", async () => {
    for (const [, headers, body] of NOT_PUBLIC_REQUESTS) {
      const response = await protectedHandler().fetch(post(headers, body ?? toolsCallBody(PROTECTED_TOOL)));

      expect(response.status).toBe(401);
    }
    expect(toolRuns).toEqual([]);
  });

  // A request that the SDK would serve as 2025-era skips its Mcp-Name check, so protect() refuses it.
  it.each<[string, boolean]>([["no token", false], ["a verified token", true]])(
    "refuses a request that the SDK would route to 2025-era serving, with %s, before the handler",
    async (_case, withToken) => {
      const fetchSpy = vi.fn(async () => new Response(null, { status: 204 }));
      const headers = [...PUBLIC_REQUESTS[1]![1], ...(withToken ? [["authorization", `Bearer ${await agent.signAgentJwt({ aud: MCP_ENDPOINT })}`] as [string, string]] : [])];

      const response = await protectedHandlerOf({ fetch: fetchSpy }).fetch(new Request(MCP_ENDPOINT, { method: "GET", headers }));

      expect(response.status).toBe(withToken ? 403 : 401);
      expect(fetchSpy).not.toHaveBeenCalled();
    },
  );

  it("refuses a request whose body was already read, which the SDK cannot classify", async () => {
    const fetchSpy = vi.fn(async () => new Response(null, { status: 204 }));
    const request = post(toolsCallHeaders(PUBLIC_TOOL), toolsCallBody(PUBLIC_TOOL));
    await request.text();

    expect((await protectedHandlerOf({ fetch: fetchSpy }).fetch(request)).status).toBe(401);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("answers 413 before dispatch for a body over the size limit", async () => {
    const fetchSpy = vi.fn(async () => new Response(null, { status: 204 }));
    const request = post(toolsCallHeaders(PUBLIC_TOOL), toolsCallBody(PUBLIC_TOOL, { padding: "x".repeat(4 * 1024 * 1024) }));

    expect((await protectedHandlerOf({ fetch: fetchSpy }).fetch(request)).status).toBe(413);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("answers 415 before it parses a body with an unsupported content type", async () => {
    const fetchSpy = vi.fn(async () => new Response(null, { status: 204 }));
    const headers = new Headers(toolsCallHeaders(PUBLIC_TOOL));
    headers.set("content-type", "application/x-www-form-urlencoded");
    const request = new Request(MCP_ENDPOINT, { method: "POST", headers, body: new URLSearchParams({ message: "hello" }) });

    expect((await protectedHandlerOf({ fetch: fetchSpy }).fetch(request)).status).toBe(415);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("refuses a request with a token for another audience before the tool runs", async () => {
    const token = await agent.signAgentJwt({ aud: "https://other.example/mcp" });
    const request = post([...toolsCallHeaders(PROTECTED_TOOL), ["authorization", `Bearer ${token}`]], toolsCallBody(PROTECTED_TOOL));

    const response = await protectedHandler().fetch(request);

    expect(response.status).toBe(401);
    expect(toolRuns).toEqual([]);
  });
});
