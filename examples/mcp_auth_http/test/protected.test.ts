import { Client, InsufficientScopeError, StreamableHTTPClientTransport } from "@modelcontextprotocol/client";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { FORBIDDEN_ACTION } from "../src/demo-identity.js";
import { EXTERNAL_SCOPE } from "../src/test-issuer.js";
import { PROTECTED_TOOL, startExampleServer, type RunningExampleServer } from "../src/server.js";

// The five cases of MCP-TS-002 through a real HTTP server and the SDK client.
const MCP_REVISION = "2026-07-28";

interface RecordedResponse {
  status: number;
  wwwAuthenticate: string | null;
  body: string;
}

let server: RunningExampleServer;

beforeAll(async () => {
  server = await startExampleServer();
});

afterAll(async () => {
  await server.close();
});

/** An SDK client pinned to 2026-07-28; `token` is the per-request mint hook. */
async function connect(token?: () => Promise<string>): Promise<{ client: Client; responses: RecordedResponse[] }> {
  const responses: RecordedResponse[] = [];
  const recordingFetch: typeof fetch = async (input, init) => {
    const response = await fetch(input, init);
    responses.push({ status: response.status, wwwAuthenticate: response.headers.get("www-authenticate"), body: await response.clone().text() });
    return response;
  };
  const transport = new StreamableHTTPClientTransport(server.url, { fetch: recordingFetch, ...(token && { authProvider: { token } }) });
  const client = new Client({ name: "exit-flow-b", version: "0.0.0" }, { versionNegotiation: { mode: { pin: MCP_REVISION } } });
  await client.connect(transport).catch(() => undefined);
  return { client, responses };
}

const validToken = () => server.demo.mintAgentJwt(server.url.href);
// The example serves RFC 9728 metadata for its external issuer, so every 401 links it.
const metadataUrl = () => new URL("/.well-known/oauth-protected-resource/mcp", server.url).href;

describe("examples/mcp_auth_http protected end to end (MCP-TS-001, MCP-TS-002)", () => {
  it("success: a granted call runs the tool", async () => {
    const { client } = await connect(validToken);

    const result = await client.callTool({ name: PROTECTED_TOOL, arguments: { action: "deploy" } });
    await client.close();

    expect(result.isError).toBeFalsy();
    expect(result.content).toEqual([{ type: "text", text: "executed: deploy" }]);
  });

  it("missing token: HTTP 401 with asap:auth_required (HTTP layer)", async () => {
    const { client, responses } = await connect();

    await expect(client.callTool({ name: PROTECTED_TOOL, arguments: { action: "deploy" } })).rejects.toThrow();
    await client.close();

    expect(responses.at(-1)).toEqual({
      status: 401,
      wwwAuthenticate: `Bearer resource_metadata="${metadataUrl()}"`,
      body: JSON.stringify({ error: "invalid_token", error_description: "asap:auth_required" }),
    });
  });

  it("invalid token: HTTP 401 with asap:invalid_token (HTTP layer), even on the public discover call", async () => {
    const { client, responses } = await connect(async () => "not-a-jwt");
    await client.close();

    // Not an Agent JWT (no typ agent+jwt), so the external-token verifier answers it.
    const description = "asap:invalid_token: malformed token";
    expect(responses.at(-1)).toEqual({
      status: 401,
      wwwAuthenticate: `Bearer error="invalid_token", error_description="${description}", resource_metadata="${metadataUrl()}"`,
      body: JSON.stringify({ error: "invalid_token", error_description: description }),
    });
  });

  it("out-of-grant call: asap:capability_denied in the tool result (HTTP 200)", async () => {
    const { client, responses } = await connect(() => server.demo.mintAgentJwt(server.url.href, ["unrelated_capability"]));

    const result = await client.callTool({ name: PROTECTED_TOOL, arguments: { action: "deploy" } });
    await client.close();

    expect(responses.at(-1)?.status).toBe(200);
    expect(result).toMatchObject({ isError: true, content: [{ type: "text", text: `asap:capability_denied: JWT capabilities claim does not include '${PROTECTED_TOOL}'` }] });
  });

  it("constraint violation: asap:constraint_violation in the tool result (HTTP 200)", async () => {
    const { client, responses } = await connect(validToken);

    const result = await client.callTool({ name: PROTECTED_TOOL, arguments: { action: FORBIDDEN_ACTION } });
    await client.close();

    expect(responses.at(-1)?.status).toBe(200);
    expect(result).toMatchObject({ isError: true, content: [{ type: "text", text: "asap:constraint_violation: action: is in the forbidden values" }] });
  });
});

// The resource-server side of MCP authorization (OAuth 2.1), with the example's local test issuer.
describe("examples/mcp_auth_http with an external OAuth 2.1 access token", () => {
  it("success: the same access token works on every request (no replay check)", async () => {
    const token = await server.testIssuer.mint(server.url.href, EXTERNAL_SCOPE);
    const { client, responses } = await connect(async () => token);

    const first = await client.callTool({ name: PROTECTED_TOOL, arguments: { action: "deploy" } });
    const second = await client.callTool({ name: PROTECTED_TOOL, arguments: { action: "restart" } });
    await client.close();

    expect([first.content, second.content]).toEqual([[{ type: "text", text: "executed: deploy" }], [{ type: "text", text: "executed: restart" }]]);
    expect(responses.every((response) => response.status === 200)).toBe(true);
  });

  it("out-of-grant call: HTTP 403 insufficient_scope with asap:capability_denied (HTTP layer)", async () => {
    const token = await server.testIssuer.mint(server.url.href, "files:read");
    const { client, responses } = await connect(async () => token);

    const failure = client.callTool({ name: PROTECTED_TOOL, arguments: { action: "deploy" } });
    await expect(failure).rejects.toBeInstanceOf(InsufficientScopeError);
    await expect(failure).rejects.toMatchObject({ requiredScope: EXTERNAL_SCOPE, errorDescription: "asap:capability_denied" });
    await client.close();

    expect(responses.at(-1)).toMatchObject({ status: 403, body: JSON.stringify({ error: "insufficient_scope", error_description: "asap:capability_denied" }) });
  });
});
