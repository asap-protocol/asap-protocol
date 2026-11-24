import { afterEach, describe, expect, it } from "vitest";

import { COMPLIANCE_PROBE_PATH, type ComplianceProbeTokens } from "../src/compliance.js";
import { PROTECTED_TOOL, startExampleServer, type RunningExampleServer } from "../src/server.js";

let server: RunningExampleServer | undefined;

afterEach(async () => {
  await server?.close();
  server = undefined;
});

/** A plain 2026-07-28 tools/call, as a non-SDK checker sends it. */
async function callProtectedTool(url: URL, token: string, args: Record<string, unknown>): Promise<Response> {
  const _meta = { "io.modelcontextprotocol/protocolVersion": "2026-07-28", "io.modelcontextprotocol/clientCapabilities": {} };
  return fetch(url, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "mcp-protocol-version": "2026-07-28",
      "mcp-method": "tools/call",
      "mcp-name": PROTECTED_TOOL,
      authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name: PROTECTED_TOOL, arguments: args, _meta } }),
  });
}

async function toolText(response: Response): Promise<string> {
  return ((await response.json()) as { result: { content: { text: string }[] } }).result.content[0]!.text;
}

const probeUrl = (running: RunningExampleServer) => new URL(COMPLIANCE_PROBE_PATH, running.url);

describe("compliance mode (test only)", () => {
  it("is off by default: the probe path is behind the token layer and gives no token", async () => {
    server = await startExampleServer();

    const response = await fetch(probeUrl(server), { method: "POST" });

    expect(response.status).toBe(401);
    expect(await response.text()).not.toContain("agent_jwt");
  });

  // Checks run twice in a row; fresh single-use tokens per round prove the provider.
  it("gives a remote checker fresh probe tokens that hold for each case, round after round", async () => {
    server = await startExampleServer({ compliance: true });

    for (let round = 0; round < 2; round += 1) {
      const probe = (await (await fetch(probeUrl(server), { method: "POST" })).json()) as ComplianceProbeTokens;
      const mcpUrl = new URL(probe.mcp_url);

      expect(await toolText(await callProtectedTool(mcpUrl, probe.agent_jwt.valid, probe.allowed_arguments))).toBe("executed: deploy");
      expect(await toolText(await callProtectedTool(mcpUrl, probe.agent_jwt.out_of_grant, probe.allowed_arguments))).toMatch(/^asap:capability_denied/);
      expect(await toolText(await callProtectedTool(mcpUrl, probe.agent_jwt.constraint_violation, probe.constraint_violation_arguments))).toMatch(/^asap:constraint_violation/);
      expect(await toolText(await callProtectedTool(mcpUrl, probe.external.valid, probe.allowed_arguments))).toBe("executed: deploy");
      expect((await callProtectedTool(mcpUrl, probe.external.out_of_grant, probe.allowed_arguments)).status).toBe(403);
    }
  });
});
