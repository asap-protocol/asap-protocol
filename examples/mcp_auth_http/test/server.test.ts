import { Client, StreamableHTTPClientTransport } from "@modelcontextprotocol/client";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { PROTECTED_TOOL, PUBLIC_TOOL, type RunningExampleServer, startExampleServer } from "../src/server.js";

// MCP revision 2026-07-28: no initialize handshake and no session.
const MCP_REVISION = "2026-07-28";

/**
 * The request a non-SDK client sends for `tools/call` (the compliance
 * harness builds its transport from this). The `_meta` envelope needs
 * the protocol version and the client capabilities; `clientInfo` is optional.
 */
function plainToolsCall(tool: string, args: Record<string, unknown>, mcpNameHeader = tool): RequestInit {
  return {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "mcp-protocol-version": MCP_REVISION,
      "mcp-method": "tools/call",
      "mcp-name": mcpNameHeader,
    },
    body: JSON.stringify({
      jsonrpc: "2.0",
      id: 1,
      method: "tools/call",
      params: {
        name: tool,
        arguments: args,
        _meta: {
          "io.modelcontextprotocol/protocolVersion": MCP_REVISION,
          "io.modelcontextprotocol/clientCapabilities": {},
        },
      },
    }),
  };
}

let server: RunningExampleServer;

beforeAll(async () => {
  server = await startExampleServer();
});

afterAll(async () => {
  await server.close();
});

describe("examples/mcp_auth_http (protocol probe, protected)", () => {
  it("serves both tools to the SDK client pinned to 2026-07-28", async () => {
    const client = new Client({ name: "probe", version: "0.0.0" }, { versionNegotiation: { mode: { pin: MCP_REVISION } } });
    await client.connect(new StreamableHTTPClientTransport(server.url));
    const { tools } = await client.listTools();
    const result = await client.callTool({ name: PUBLIC_TOOL, arguments: { message: "hello" } });
    await client.close();

    expect(tools.map((tool) => tool.name).sort()).toEqual([PUBLIC_TOOL, PROTECTED_TOOL].sort());
    expect(result.content).toEqual([{ type: "text", text: "hello" }]);
  });

  it("answers a plain fetch POST with no handshake", async () => {
    const response = await fetch(server.url, plainToolsCall(PUBLIC_TOOL, { message: "plain" }));

    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toBe("application/json");
    expect(await response.json()).toEqual({
      jsonrpc: "2.0",
      id: 1,
      result: {
        content: [{ type: "text", text: "plain" }],
        resultType: "complete",
        _meta: { "io.modelcontextprotocol/serverInfo": { name: "mcp-auth-http-example", version: "0.1.0" } },
      },
    });
  });

  it("refuses an Mcp-Name header that disagrees with the body", async () => {
    const response = await fetch(server.url, plainToolsCall(PROTECTED_TOOL, { action: "x" }, PUBLIC_TOOL));

    expect(response.status).toBe(400);
    expect((await response.json()).error.code).toBe(-32020);
  });

  // The token layer refuses it first: 401 with no token, and 403 with a valid token (deny by default).
  it("refuses a 2025-era request that has no _meta envelope", async () => {
    const legacyCall = (headers: Record<string, string>) =>
      fetch(server.url, {
        method: "POST",
        headers: { "content-type": "application/json", "mcp-method": "tools/call", "mcp-name": PUBLIC_TOOL, ...headers },
        body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name: PROTECTED_TOOL } }),
      });

    expect((await legacyCall({})).status).toBe(401);
    const response = await legacyCall({ authorization: `Bearer ${await server.demo.mintAgentJwt(server.url.href)}` });
    expect(response.status).toBe(403);
    expect(await response.json()).toEqual({ error: "insufficient_scope", error_description: "asap:capability_denied" });
  });
});
