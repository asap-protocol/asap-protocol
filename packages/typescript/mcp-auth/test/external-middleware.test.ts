import { createAgent, createHost, MemoryStorage, type AgentContext } from "@asap-protocol/client";
import type { AuthInfo, JSONRPCRequest, ServerContext } from "@modelcontextprotocol/server";
import { beforeAll, describe, expect, it, vi } from "vitest";

import { createMcpAuthMiddleware, toolErrorResult, type CallToolResult, type McpAuthConfig } from "../src/index.js";
import { LocalTestIssuer } from "./local-issuer.js";
import { MCP_ENDPOINT, post, PROTECTED_TOOL, PUBLIC_TOOL, toolsCallHeaders } from "./mcp-requests.js";

const ISSUER = "https://issuer.example";
const METADATA_URL = "https://mcp.example.com/.well-known/oauth-protected-resource/mcp";
const OK: CallToolResult = { content: [{ type: "text", text: "ran" }] };

let issuer: LocalTestIssuer;
let agent: AgentContext;

beforeAll(async () => {
  issuer = await LocalTestIssuer.create(ISSUER);
  agent = await createAgent(await createHost({ storage: new MemoryStorage() }), { mode: "delegated" });
});

const resolveAgent = vi.fn(async ({ agentId }: { agentId: string }) => (agentId === agent.agentId ? { publicKey: agent.publicJwk, usable: true } : undefined));

function middleware(overrides: Partial<McpAuthConfig> = {}) {
  return createMcpAuthMiddleware({
    agentJwt: { audience: MCP_ENDPOINT, resolveAgent },
    externalTokens: {
      issuers: [{
        issuer: ISSUER,
        audience: MCP_ENDPOINT,
        jwks: issuer.jwks,
        grantRules: [
          { scope: "actions:run", capability: PROTECTED_TOOL, constraints: { action: { not_in: ["drop"] } } },
          { claim: "groups", value: "auditors", capability: "audit" },
        ],
      }, { issuer: "https://no-rules.example", audience: MCP_ENDPOINT, jwks: issuer.jwks }],
    },
    publicTools: [PUBLIC_TOOL],
    toolCapabilities: { [PROTECTED_TOOL]: PROTECTED_TOOL, audit: "audit" },
    resourceMetadataUrl: METADATA_URL,
    ...overrides,
  });
}

const externalToken = (scope = "actions:run", extra = {}) => issuer.mint(issuer.claims(MCP_ENDPOINT, { scope, ...extra }));
const authenticate = (auth: ReturnType<typeof middleware>, token: string) => auth.authenticate(post([...toolsCallHeaders(PROTECTED_TOOL), ["authorization", `Bearer ${token}`]]));

describe("createMcpAuthMiddleware with both token types: one verifier per token, chosen by typ", () => {
  it("sends an Agent JWT to the Agent JWT verifier and an access token to the external verifier", async () => {
    const auth = middleware();

    expect(await authenticate(auth, await agent.signAgentJwt({ aud: MCP_ENDPOINT }))).toMatchObject({ extra: { principal: { kind: "agent_jwt" } } });
    expect(await authenticate(auth, await externalToken())).toMatchObject({ resourceMetadataUrl: METADATA_URL, extra: { principal: { kind: "external" } } });
  });

  it.each<[string, () => Promise<string>, Partial<McpAuthConfig>, string]>([
    ["an access token labeled agent+jwt goes to the Agent JWT verifier only", () => issuer.mint(issuer.claims(MCP_ENDPOINT, { sub: "user-1" }), { typ: "agent+jwt", alg: "ES256" }), {}, "alg is not EdDSA or Ed25519"],
    ["a string that is not a JWT goes to the external verifier only", async () => "not-a-jwt", {}, "malformed token"],
    ["a refused access token is not retried as an Agent JWT", () => externalToken("actions:run", { exp: 1 }), {}, "token expired"],
    ["an Agent JWT is refused when only external tokens are configured", () => agent.signAgentJwt({ aud: MCP_ENDPOINT }), { agentJwt: undefined }, "token type not accepted"],
  ])("%s", async (_case, makeToken, overrides, reason) => {
    resolveAgent.mockClear();

    const response = await authenticate(middleware(overrides), await makeToken());

    expect(response).toBeInstanceOf(Response);
    expect(await (response as Response).json()).toEqual({ error: "invalid_token", error_description: `asap:invalid_token: ${reason}` });
    expect(resolveAgent).not.toHaveBeenCalled();
  });

  it("accepts the same access token on every request (no replay check)", async () => {
    const auth = middleware();
    const token = await externalToken();

    expect(await authenticate(auth, token)).not.toBeInstanceOf(Response);
    expect(await authenticate(auth, token)).not.toBeInstanceOf(Response);
  });

  it("refuses a config with no token type", () => {
    expect(() => middleware({ agentJwt: undefined, externalTokens: undefined })).toThrow("agentJwt, externalTokens, or both");
  });

  // MCP authorization: a protected MCP server MUST serve RFC 9728 metadata, and with external tokens this server is the resource server.
  it("refuses externalTokens with no resourceMetadataUrl", () => {
    expect(() => middleware({ resourceMetadataUrl: undefined })).toThrow("externalTokens needs resourceMetadataUrl");
  });
});

const base64url = (value: unknown) => Buffer.from(JSON.stringify(value)).toString("base64url");

/** Tokens that no verifier can decode. The last has a readable agent+jwt header, so with both types it goes to the Agent JWT verifier. */
const GARBAGE_TOKENS: [string, string][] = [
  ["a string that is not a JWT", "not-a-jwt"],
  ["three segments that are not base64url JSON", "a.b.c"],
  ["an agent+jwt header with a payload that is not JSON", `${base64url({ alg: "EdDSA", typ: "agent+jwt" })}.bm90LWpzb24.c2ln`],
];

describe("a garbage token gets one client-visible reason in every configuration", () => {
  it.each<[string, Partial<McpAuthConfig>]>([
    ["Agent JWTs only", { externalTokens: undefined }],
    ["external tokens only", { agentJwt: undefined }],
    ["both token types", {}],
  ])("%s: 401 asap:invalid_token: malformed token", async (_config, overrides) => {
    for (const [, token] of GARBAGE_TOKENS) {
      const response = await authenticate(middleware(overrides), token);

      expect(await (response as Response).json()).toEqual({ error: "invalid_token", error_description: "asap:invalid_token: malformed token" });
    }
  });
});

async function externalAuthInfo(scope: string, extra = {}): Promise<AuthInfo> {
  return (await middleware().authenticate(post([...toolsCallHeaders(PROTECTED_TOOL), ["authorization", `Bearer ${await externalToken(scope, extra)}`]]))) as AuthInfo;
}

function toolsCall(tool: string, method = "tools/call"): JSONRPCRequest {
  return { jsonrpc: "2.0", id: 1, method, params: { name: tool, arguments: {} } };
}

describe("scopeChallenge: HTTP 403 insufficient_scope for a missing grant on the external-token path", () => {
  it("asks for the scope that maps to the tool's capability", async () => {
    const challenge = await middleware().scopeChallenge({ request: toolsCall(PROTECTED_TOOL), authInfo: await externalAuthInfo("files:read") });

    expect(challenge).toEqual({ scopes: ["actions:run"], errorDescription: "asap:capability_denied" });
  });

  it.each<[string, () => Promise<{ request: JSONRPCRequest; authInfo?: AuthInfo }>]>([
    ["a granted call", async () => ({ request: toolsCall(PROTECTED_TOOL), authInfo: await externalAuthInfo("actions:run") })],
    ["a capability that no scope grants (the tool result answers)", async () => ({ request: toolsCall("audit"), authInfo: await externalAuthInfo("files:read") })],
    ["an Agent JWT principal (grant errors stay tool results)", async () => ({ request: toolsCall(PROTECTED_TOOL), authInfo: (await authenticate(middleware(), await agent.signAgentJwt({ aud: MCP_ENDPOINT }))) as AuthInfo })],
    ["no auth info (the grant layer fails closed)", async () => ({ request: toolsCall(PROTECTED_TOOL) })],
    ["a public tool", async () => ({ request: toolsCall(PUBLIC_TOOL), authInfo: await externalAuthInfo("files:read") })],
    ["a method other than tools/call", async () => ({ request: toolsCall(PROTECTED_TOOL, "prompts/get"), authInfo: await externalAuthInfo("files:read") })],
  ])("does not challenge %s", async (_case, context) => {
    expect(await middleware().scopeChallenge(await context())).toBeUndefined();
  });
});

describe("protectTool for an external principal", () => {
  async function callTool(authInfo: AuthInfo, action: string) {
    const handler = vi.fn(async (_args: { action: string }, _ctx: ServerContext) => OK);
    const req = new Request(MCP_ENDPOINT, { headers: { "mcp-name": PROTECTED_TOOL } });
    const result = await middleware().protectTool(PROTECTED_TOOL, handler)({ action }, { http: { req, authInfo } } as unknown as ServerContext);
    return { result, handler };
  }

  it("runs a call that the token's scopes grant", async () => {
    expect((await callTool(await externalAuthInfo("actions:run"), "deploy")).result).toEqual(OK);
  });

  it("answers a constraint violation in the tool result, as on the Agent JWT path", async () => {
    const { result, handler } = await callTool(await externalAuthInfo("actions:run"), "drop");

    expect(result).toEqual(toolErrorResult("asap:constraint_violation", "action: is in the forbidden values"));
    expect(handler).not.toHaveBeenCalled();
  });

  it("denies in the tool result when no scope challenge ran", async () => {
    const { result } = await callTool(await externalAuthInfo("files:read"), "deploy");

    expect(result).toEqual(toolErrorResult("asap:capability_denied", `no active grant for capability '${PROTECTED_TOOL}'`));
  });
});
