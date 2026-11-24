import { Client, StreamableHTTPClientTransport } from "@modelcontextprotocol/client";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { FORBIDDEN_ACTION } from "../src/demo-identity.js";
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
      wwwAuthenticate: "Bearer",
      body: JSON.stringify({ error: "invalid_token", error_description: "asap:auth_required" }),
    });
  });

  it("invalid token: HTTP 401 with asap:invalid_token (HTTP layer), even on the public discover call", async () => {
    const { client, responses } = await connect(async () => "not-a-jwt");
    await client.close();

    const description = "asap:invalid_token: invalid JWT structure";
    expect(responses.at(-1)).toEqual({
      status: 401,
      wwwAuthenticate: `Bearer error="invalid_token", error_description="${description}"`,
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
