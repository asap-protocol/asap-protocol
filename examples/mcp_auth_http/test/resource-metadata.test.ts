import { afterEach, describe, expect, it } from "vitest";

import { PROTECTED_TOOL, PUBLIC_TOOL, startExampleServer, type RunningExampleServer } from "../src/server.js";
import { EXTERNAL_SCOPE, TEST_ISSUER } from "../src/test-issuer.js";

let server: RunningExampleServer | undefined;

afterEach(async () => {
  await server?.close();
  server = undefined;
});

/** RFC 9728 path-aware well-known URL of the MCP endpoint. */
const metadataUrl = (running: RunningExampleServer) => new URL("/.well-known/oauth-protected-resource/mcp", running.url);

function toolsCall(url: URL, tool: string): Promise<Response> {
  const _meta = { "io.modelcontextprotocol/protocolVersion": "2026-07-28", "io.modelcontextprotocol/clientCapabilities": {} };
  return fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json", "mcp-protocol-version": "2026-07-28", "mcp-method": "tools/call", "mcp-name": tool },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name: tool, arguments: { message: "hi" }, _meta } }),
  });
}

describe("RFC 9728 protected-resource metadata (MCP authorization, resource-server side)", () => {
  it("serves a document that names this MCP endpoint and only the issuer it accepts tokens from, with no token", async () => {
    server = await startExampleServer();

    const response = await fetch(metadataUrl(server));

    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toBe("application/json");
    expect(await response.json()).toEqual({
      resource: server.url.href,
      authorization_servers: [TEST_ISSUER],
      scopes_supported: [EXTERNAL_SCOPE],
      bearer_methods_supported: ["header"],
    });
  });

  it("links the document from the 401 challenge", async () => {
    server = await startExampleServer();

    const response = await toolsCall(server.url, PROTECTED_TOOL);

    expect(response.status).toBe(401);
    expect(response.headers.get("www-authenticate")).toBe(`Bearer resource_metadata="${metadataUrl(server).href}"`);
  });

  it("does not treat the metadata path as an MCP call, even with public tool headers", async () => {
    server = await startExampleServer();

    const response = await toolsCall(metadataUrl(server), PUBLIC_TOOL);

    expect(response.status).toBe(405);
    expect(response.headers.get("allow")).toBe("GET, HEAD");
  });

  it("keeps the example's Origin check on the metadata path", async () => {
    server = await startExampleServer();

    expect((await fetch(metadataUrl(server), { headers: { origin: "https://evil.example" } })).status).toBe(403);
  });

  it("serves no document and no resource_metadata when no external issuer is configured (Agent JWT only)", async () => {
    server = await startExampleServer({ externalTokens: false });

    expect((await fetch(metadataUrl(server))).status).toBe(404);
    expect((await toolsCall(server.url, PROTECTED_TOOL)).headers.get("www-authenticate")).toBe("Bearer");
  });
});
