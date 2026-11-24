import { createAgent, createHost, MemoryStorage, type AgentContext } from "@asap-protocol/client";
import { createMcpHandler, McpServer } from "@modelcontextprotocol/server";
import { beforeAll, beforeEach, describe, expect, it } from "vitest";

import { createMcpAuthMiddleware, MCP_PROTOCOL_VERSION, type McpAuthMiddleware } from "../src/index.js";
import { MCP_ENDPOINT, PROTECTED_TOOL, PUBLIC_TOOL, toolsCallBody, type HeaderList } from "./mcp-requests.js";

/**
 * The header rule trusts `Mcp-Method` and
 * `Mcp-Name` only because SDK 2.4 checks them against the body on a
 * 2026-07-28 request (docs/adapters/mcp-auth-bridge.md). These forged requests go
 * through a real `createMcpHandler` in both legacy modes, so the CI row that
 * tests the newest SDK 2.x fails if an SDK change lets one of them run a tool.
 */
const UNWRAPPED_TOOL = "unwrapped_tool";
const LEGACY_MODES = ["stateless", "reject"] as const;

let agent: AgentContext;
let toolRuns: string[];

beforeAll(async () => {
  agent = await createAgent(await createHost({ storage: new MemoryStorage() }), { mode: "delegated" });
});

beforeEach(() => {
  toolRuns = [];
});

function buildServer(auth: McpAuthMiddleware): McpServer {
  const server = new McpServer({ name: "mcp-auth-forged-test", version: "0.0.0" });
  const tool = (name: string) => async () => {
    toolRuns.push(name);
    return { content: [{ type: "text" as const, text: name }] };
  };
  server.registerTool(PUBLIC_TOOL, { description: "public" }, tool(PUBLIC_TOOL));
  server.registerTool(PROTECTED_TOOL, { description: "protected" }, auth.protectTool(PROTECTED_TOOL, tool(PROTECTED_TOOL)));
  server.registerTool(UNWRAPPED_TOOL, { description: "registered without protectTool" }, tool(UNWRAPPED_TOOL));
  return server;
}

function app(legacy: (typeof LEGACY_MODES)[number]) {
  const auth = createMcpAuthMiddleware({
    agentJwt: { audience: MCP_ENDPOINT, resolveAgent: async ({ agentId }) => (agentId === agent.agentId ? { publicKey: agent.publicJwk, usable: true } : undefined) },
    publicTools: [PUBLIC_TOOL],
    toolCapabilities: { [PROTECTED_TOOL]: PROTECTED_TOOL },
  });
  return auth.protect(createMcpHandler(() => buildServer(auth), { legacy }));
}

const JSON_TYPE = "application/json";
const base64 = (value: string) => `=?base64?${Buffer.from(value).toString("base64")}?=`;
const same = (claimed: string) => claimed;
const legacyCall = (tool: string) => ({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name: tool, arguments: {} } });
const envelope2025 = (tool: string) => ({ ...legacyCall(tool), params: { name: tool, arguments: {}, _meta: { "io.modelcontextprotocol/protocolVersion": "2025-11-25" } } });
const initialize = () => ({ jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2025-11-25", capabilities: {}, clientInfo: { name: "x", version: "0" } } });
const notificationOf = (method: string, params: Record<string, unknown>) => ({ jsonrpc: "2.0", method, params: { ...params, _meta: { "io.modelcontextprotocol/protocolVersion": MCP_PROTOCOL_VERSION } } });

/** The token layer refuses the last two itself; with the first, it relies on the SDK's header check. */
const VERSION_HEADERS: [string, HeaderList][] = [
  ["MCP-Protocol-Version 2026-07-28", [["mcp-protocol-version", MCP_PROTOCOL_VERSION]]],
  ["MCP-Protocol-Version 2025-11-25", [["mcp-protocol-version", "2025-11-25"]]],
  ["no MCP-Protocol-Version", []],
];

/** [case, HTTP method, content type, Mcp-Method, Mcp-Name for the claimed tool, body that calls the target tool]. */
type ForgedRequest = [string, string, string, string, (claimed: string) => string, (target: string) => unknown];

const FORGED_REQUESTS: ForgedRequest[] = [
  ["a batch", "POST", JSON_TYPE, "tools/call", same, (target) => [toolsCallBody(target)]],
  ["a body with no _meta envelope", "POST", JSON_TYPE, "tools/call", same, legacyCall],
  ["a 2025 _meta envelope", "POST", JSON_TYPE, "tools/call", same, envelope2025],
  ["initialize", "POST", JSON_TYPE, "tools/call", same, initialize],
  ["a method mismatch", "POST", JSON_TYPE, "tools/list", same, toolsCallBody],
  ["a name mismatch", "POST", JSON_TYPE, "tools/call", same, toolsCallBody],
  ["a base64 Mcp-Name", "POST", JSON_TYPE, "tools/call", base64, toolsCallBody],
  ["a notification of another method", "POST", JSON_TYPE, "tools/call", same, () => notificationOf("notifications/cancelled", { requestId: 1 })],
  ["GET", "GET", JSON_TYPE, "tools/call", same, () => undefined],
  ["DELETE", "DELETE", JSON_TYPE, "tools/call", same, () => undefined],
  ["PUT", "PUT", JSON_TYPE, "tools/call", same, toolsCallBody],
  ["text/plain", "POST", "text/plain", "tools/call", same, toolsCallBody],
];

/** A forged request whose headers claim `claimed` and whose body calls the unwrapped tool. */
function forged([, httpMethod, contentType, mcpMethod, mcpName, body]: ForgedRequest, version: HeaderList, claimed: string, extra: HeaderList = []): Request {
  const payload = body(UNWRAPPED_TOOL);
  return new Request(MCP_ENDPOINT, {
    method: httpMethod,
    headers: [["content-type", contentType], ["accept", "application/json, text/event-stream"], ...version, ["mcp-method", mcpMethod], ["mcp-name", mcpName(claimed)], ...extra],
    body: payload === undefined ? undefined : JSON.stringify(payload),
  });
}

describe.each(LEGACY_MODES)("forged requests through createMcpHandler (legacy: %s)", (legacy) => {
  describe.each(VERSION_HEADERS)("with %s", (_version, version) => {
    it.each(FORGED_REQUESTS)("refuses %s that claims the public tool, with no token, and runs no tool", async (...request) => {
      const response = await app(legacy).fetch(forged(request, version, PUBLIC_TOOL));

      expect(response.status).toBeGreaterThanOrEqual(400);
      expect(toolRuns).toEqual([]);
    });

    it.each(FORGED_REQUESTS)("refuses %s that claims the declared tool, with a verified token, and runs no tool", async (...request) => {
      const token = await agent.signAgentJwt({ aud: MCP_ENDPOINT, capabilities: [PROTECTED_TOOL] });

      const response = await app(legacy).fetch(forged(request, version, PROTECTED_TOOL, [["authorization", `Bearer ${token}`]]));

      expect(response.status).toBeGreaterThanOrEqual(400);
      expect(toolRuns).toEqual([]);
    });
  });

  // SDK 2.4 skips the header check for a notification and acknowledges it with 202; no tool runs from a notification.
  it("acknowledges a tools/call notification that claims the public tool, and runs no tool", async () => {
    const notification: ForgedRequest = ["", "POST", JSON_TYPE, "tools/call", same, (target) => notificationOf("tools/call", { name: target, arguments: {} })];

    const response = await app(legacy).fetch(forged(notification, VERSION_HEADERS[0]![1], PUBLIC_TOOL));

    expect(response.status).toBe(202);
    expect(toolRuns).toEqual([]);
  });
});
