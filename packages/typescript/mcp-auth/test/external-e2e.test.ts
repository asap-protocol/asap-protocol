import {
  createMcpHandler,
  getOAuthProtectedResourceMetadataUrl,
  McpServer,
  oauthMetadataResponse,
  type AuthMetadataOptions,
} from "@modelcontextprotocol/server";
import { beforeAll, beforeEach, describe, expect, it } from "vitest";
import * as z from "zod";

import { createMcpAuthMiddleware, type McpAuthMiddleware } from "../src/index.js";
import { LocalTestIssuer } from "./local-issuer.js";
import { MCP_ENDPOINT, post, PROTECTED_TOOL, toolsCallBody, toolsCallHeaders } from "./mcp-requests.js";

const ISSUER = "https://issuer.example";
const SCOPE = "actions:run";
// RFC 9728 through the SDK helpers: the path-aware well-known URL of the MCP endpoint.
const METADATA_URL = getOAuthProtectedResourceMetadataUrl(new URL(MCP_ENDPOINT));
const METADATA_OPTIONS: AuthMetadataOptions = {
  oauthMetadata: { issuer: ISSUER, authorization_endpoint: `${ISSUER}/authorize`, token_endpoint: `${ISSUER}/token`, response_types_supported: ["code"] },
  resourceServerUrl: new URL(MCP_ENDPOINT),
  scopesSupported: [SCOPE],
};

let issuer: LocalTestIssuer;
let toolRuns: string[];

beforeAll(async () => {
  issuer = await LocalTestIssuer.create(ISSUER);
});

beforeEach(() => {
  toolRuns = [];
});

function buildServer(auth: McpAuthMiddleware): McpServer {
  const server = new McpServer({ name: "mcp-auth-external-test", version: "0.0.0" });
  server.registerTool(
    PROTECTED_TOOL,
    { description: "protected", inputSchema: z.object({ action: z.string() }), scopeChallenge: auth.scopeChallenge },
    auth.protectTool(PROTECTED_TOOL, async ({ action }) => {
      toolRuns.push(action);
      return { content: [{ type: "text", text: `executed: ${action}` }] };
    }),
  );
  return server;
}

/** The MCP endpoint behind the token layer, and the SDK's metadata documents beside it. */
function app(): { fetch(request: Request): Promise<Response> } {
  const auth = createMcpAuthMiddleware({
    externalTokens: {
      issuers: [{ issuer: ISSUER, audience: MCP_ENDPOINT, jwks: issuer.jwks, grantRules: [{ scope: SCOPE, capability: PROTECTED_TOOL, constraints: { action: { not_in: ["drop"] } } }] }],
    },
    toolCapabilities: { [PROTECTED_TOOL]: PROTECTED_TOOL },
    resourceMetadataUrl: METADATA_URL,
  });
  const mcp = auth.protect(createMcpHandler(() => buildServer(auth), { legacy: "reject" }));
  return { fetch: async (request) => oauthMetadataResponse(request, METADATA_OPTIONS) ?? mcp.fetch(request) };
}

async function callTool(scope: string | undefined, action: string, token?: string): Promise<Response> {
  const bearer = token ?? (await issuer.mint(issuer.claims(MCP_ENDPOINT, { scope })));
  return app().fetch(post([...toolsCallHeaders(PROTECTED_TOOL), ["authorization", `Bearer ${bearer}`]], toolsCallBody(PROTECTED_TOOL, { action })));
}

describe("external OAuth 2.1 tokens through createMcpHandler (MCP authorization, resource-server side)", () => {
  it("points a request with no token at RFC 9728 metadata that names the issuer", async () => {
    const challenge = await app().fetch(post(toolsCallHeaders(PROTECTED_TOOL)));
    expect(challenge.status).toBe(401);
    expect(challenge.headers.get("www-authenticate")).toBe(`Bearer resource_metadata="${METADATA_URL}"`);

    const metadata = await app().fetch(new Request(METADATA_URL));

    expect(metadata.status).toBe(200);
    expect(await metadata.json()).toMatchObject({ resource: MCP_ENDPOINT, authorization_servers: [ISSUER], scopes_supported: [SCOPE] });
  });

  it("runs a granted call, and the same access token works twice", async () => {
    const token = await issuer.mint(issuer.claims(MCP_ENDPOINT, { scope: SCOPE }));

    for (const action of ["deploy", "restart"]) {
      const response = await callTool(undefined, action, token);
      expect(response.status).toBe(200);
      expect(((await response.json()) as { result: { content: unknown } }).result.content).toEqual([{ type: "text", text: `executed: ${action}` }]);
    }
    expect(toolRuns).toEqual(["deploy", "restart"]);
  });

  it("answers a call that its scopes do not grant with HTTP 403 insufficient_scope before the tool runs", async () => {
    const response = await callTool("files:read", "deploy");

    expect(response.status).toBe(403);
    expect(response.headers.get("www-authenticate")).toBe(
      `Bearer error="insufficient_scope", error_description="asap:capability_denied", scope="${SCOPE}", resource_metadata="${METADATA_URL}"`,
    );
    expect(await response.json()).toEqual({ error: "insufficient_scope", error_description: "asap:capability_denied" });
    expect(toolRuns).toEqual([]);
  });

  it("answers a constraint violation in the tool result", async () => {
    const response = await callTool(SCOPE, "drop");

    expect(response.status).toBe(200);
    expect(((await response.json()) as { result: unknown }).result).toMatchObject({
      isError: true,
      content: [{ type: "text", text: "asap:constraint_violation: action: is in the forbidden values" }],
    });
    expect(toolRuns).toEqual([]);
  });

  it("answers a token for another audience with HTTP 401 invalid_token", async () => {
    const response = await callTool(undefined, "deploy", await issuer.mint(issuer.claims("https://other.example/mcp", { scope: SCOPE })));

    expect(response.status).toBe(401);
    expect(await response.json()).toEqual({ error: "invalid_token", error_description: "asap:invalid_token: audience mismatch" });
  });
});
