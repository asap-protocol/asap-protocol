import { readFileSync } from "node:fs";

import { createAgent, createHost, MemoryStorage, type AgentContext } from "@asap-protocol/client";
import { createMcpHandler, McpServer } from "@modelcontextprotocol/server";
import { beforeAll, beforeEach, describe, expect, it } from "vitest";
import * as z from "zod";

import { createMcpAuthMiddleware, staticAgentGrants, type McpAuthMiddleware } from "../src/index.js";
import { MCP_ENDPOINT, post, PROTECTED_TOOL, toolsCallBody, toolsCallHeaders } from "./mcp-requests.js";

const CAPABILITY = "secure_action";
const FORBIDDEN_ACTION = "drop-tables";

const VECTOR_URL = new URL("../../../../tests/fixtures/interop/mcp-auth-errors.json", import.meta.url);
const errorVector = JSON.parse(readFileSync(VECTOR_URL, "utf8")) as { tool_error_results: { code: string; result: unknown }[] };

let agent: AgentContext;
let toolRuns: string[];

beforeAll(async () => {
  agent = await createAgent(await createHost({ storage: new MemoryStorage() }), { mode: "delegated" });
});

beforeEach(() => {
  toolRuns = [];
});

function middleware(): McpAuthMiddleware {
  return createMcpAuthMiddleware({
    agentJwt: {
      audience: MCP_ENDPOINT,
      resolveAgent: async ({ agentId }) => (agentId === agent.agentId ? { publicKey: agent.publicJwk, usable: true } : undefined),
    },
    toolCapabilities: { [PROTECTED_TOOL]: CAPABILITY },
    agentGrants: staticAgentGrants({ [agent.agentId]: [{ capability: CAPABILITY, constraints: { action: { not_in: [FORBIDDEN_ACTION] } } }] }),
  });
}

/** A real SDK server; the protected tool has an inputSchema, so the SDK passes (args, ctx). */
function buildServer(auth: McpAuthMiddleware): McpServer {
  const server = new McpServer({ name: "mcp-auth-grant-test", version: "0.0.0" });
  server.registerTool(
    PROTECTED_TOOL,
    { description: "protected", inputSchema: z.object({ action: z.string() }) },
    auth.protectTool(PROTECTED_TOOL, async ({ action }) => {
      toolRuns.push(action);
      return { content: [{ type: "text", text: `executed: ${action}` }] };
    }),
  );
  return server;
}

async function callProtectedTool(capabilities: string[], action: string, authorize = true) {
  const auth = middleware();
  const handler = createMcpHandler(() => buildServer(auth), { legacy: "reject" });
  const headers = toolsCallHeaders(PROTECTED_TOOL);
  if (authorize) headers.push(["authorization", `Bearer ${await agent.signAgentJwt({ aud: MCP_ENDPOINT, capabilities })}`]);
  const request = post(headers, toolsCallBody(PROTECTED_TOOL, { action }));
  // Without authorize, the request skips the token layer: the grant layer alone must refuse it.
  const response = await (authorize ? auth.protect(handler).fetch(request) : handler.fetch(request));
  expect(response.status).toBe(200);
  return ((await response.json()) as { result: { content: unknown; isError?: boolean } }).result;
}

describe("protectTool behind createMcpHandler (grant errors are tool results, MCP-TS-002)", () => {
  it("runs a granted call with the parsed arguments", async () => {
    const result = await callProtectedTool([CAPABILITY], "deploy");

    expect(result.content).toEqual([{ type: "text", text: "executed: deploy" }]);
    expect(toolRuns).toEqual(["deploy"]);
  });

  it("answers an out-of-grant call with asap:capability_denied in the tool result", async () => {
    const result = await callProtectedTool(["unrelated_capability"], "deploy");

    expect(result).toMatchObject({ isError: true, content: [{ type: "text", text: `asap:capability_denied: JWT capabilities claim does not include '${CAPABILITY}'` }] });
    expect(toolRuns).toEqual([]);
  });

  it("answers a constraint violation with asap:constraint_violation in the tool result", async () => {
    const result = await callProtectedTool([CAPABILITY], FORBIDDEN_ACTION);

    expect(result).toMatchObject({ isError: true, content: [{ type: "text", text: "asap:constraint_violation: action: is in the forbidden values" }] });
    expect(toolRuns).toEqual([]);
  });

  it("fails closed with asap:auth_required when a protected tool is reached with no auth info", async () => {
    const result = await callProtectedTool([CAPABILITY], "deploy", false);

    expect(result).toMatchObject(errorVector.tool_error_results.find((entry) => entry.code === "asap:auth_required")!.result as object);
    expect(toolRuns).toEqual([]);
  });
});
