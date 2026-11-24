import { createAgent, createHost, MemoryStorage, type AgentContext } from "@asap-protocol/client";
import { completable, createMcpHandler, McpServer } from "@modelcontextprotocol/server";
import { beforeAll, beforeEach, describe, expect, it } from "vitest";
import * as z from "zod";

import { createMcpAuthMiddleware, type McpAuthConfig, type McpAuthMiddleware } from "../src/index.js";
import { LocalTestIssuer } from "./local-issuer.js";
import { MCP_ENDPOINT, mcpRequest, post, PROTECTED_TOOL, PUBLIC_TOOL, toolsCallBody, toolsCallHeaders, type HeaderList } from "./mcp-requests.js";

// Deny by default: a verified token reaches only declared tools and allowed methods.
const ISSUER = "https://issuer.example";
const METADATA_URL = "https://mcp.example.com/.well-known/oauth-protected-resource/mcp";
const UNWRAPPED_TOOL = "unwrapped_tool";
const RESOURCE_URI = "file:///secret.txt";
const PROMPT = "secret_prompt";
const DENIED_BODY = { error: "insufficient_scope", error_description: "asap:capability_denied" };

let agent: AgentContext;
let issuer: LocalTestIssuer;
let handlerRuns: string[];

beforeAll(async () => {
  agent = await createAgent(await createHost({ storage: new MemoryStorage() }), { mode: "delegated" });
  issuer = await LocalTestIssuer.create(ISSUER);
});

beforeEach(() => {
  handlerRuns = [];
});

function ran(name: string): { content: { type: "text"; text: string }[] } {
  handlerRuns.push(name);
  return { content: [{ type: "text", text: name }] };
}

/** Every handler records its run, so a refused request must leave `handlerRuns` empty. */
function buildServer(auth: McpAuthMiddleware): McpServer {
  const server = new McpServer({ name: "mcp-auth-deny-test", version: "0.0.0" });
  server.registerTool(PUBLIC_TOOL, { description: "public" }, async () => ran(PUBLIC_TOOL));
  server.registerTool(PROTECTED_TOOL, { description: "protected" }, auth.protectTool(PROTECTED_TOOL, async (_ctx) => ran(PROTECTED_TOOL)));
  server.registerTool(UNWRAPPED_TOOL, { description: "registered without protectTool" }, async () => ran(UNWRAPPED_TOOL));
  server.registerResource("secret", RESOURCE_URI, {}, async () => {
    handlerRuns.push("resources/read");
    return { contents: [{ uri: RESOURCE_URI, text: "secret" }] };
  });
  const topic = completable(z.string(), () => {
    handlerRuns.push("completion/complete");
    return ["alpha"];
  });
  server.registerPrompt(PROMPT, { argsSchema: z.object({ topic }) }, async () => {
    handlerRuns.push("prompts/get");
    return { messages: [{ role: "user", content: { type: "text", text: "secret" } }] };
  });
  return server;
}

function app(overrides: Partial<McpAuthConfig> = {}) {
  const auth = createMcpAuthMiddleware({
    agentJwt: { audience: MCP_ENDPOINT, resolveAgent: async ({ agentId }) => (agentId === agent.agentId ? { publicKey: agent.publicJwk, usable: true } : undefined) },
    externalTokens: { issuers: [{ issuer: ISSUER, audience: MCP_ENDPOINT, jwks: issuer.jwks, grantRules: [{ scope: "actions:run", capability: "secure_action" }] }] },
    publicTools: [PUBLIC_TOOL],
    toolCapabilities: { [PROTECTED_TOOL]: "secure_action" },
    resourceMetadataUrl: METADATA_URL,
    ...overrides,
  });
  return auth.protect(createMcpHandler(() => buildServer(auth), { legacy: "reject" }));
}

/** Tokens that verify but carry no grant: no capabilities claim, and a scope that no rule maps. */
const ZERO_GRANT_TOKENS: [string, () => Promise<string>][] = [
  ["a zero-grant Agent JWT", () => agent.signAgentJwt({ aud: MCP_ENDPOINT, capabilities: [] })],
  ["a zero-grant external token", () => issuer.mint(issuer.claims(MCP_ENDPOINT, { scope: "files:read" }))],
];

const bearer = (token: string): HeaderList => [["authorization", `Bearer ${token}`]];

const UNDECLARED_REQUESTS: [string, (token: string) => Request][] = [
  ["tools/call for a tool registered without protectTool", (token) => mcpRequest("tools/call", { name: UNWRAPPED_TOOL, arguments: {} }, UNWRAPPED_TOOL, bearer(token))],
  ["tools/call for a tool missing from the config", (token) => mcpRequest("tools/call", { name: "missing_tool", arguments: {} }, "missing_tool", bearer(token))],
  ["resources/read", (token) => mcpRequest("resources/read", { uri: RESOURCE_URI }, RESOURCE_URI, bearer(token))],
  ["prompts/get", (token) => mcpRequest("prompts/get", { name: PROMPT, arguments: { topic: "x" } }, PROMPT, bearer(token))],
  ["completion/complete", (token) => mcpRequest("completion/complete", { ref: { type: "ref/prompt", name: PROMPT }, argument: { name: "topic", value: "a" } }, undefined, bearer(token))],
];

describe.each(ZERO_GRANT_TOKENS)("deny by default after %s verifies", (_token, mint) => {
  it.each(UNDECLARED_REQUESTS)("answers %s with HTTP 403 asap:capability_denied before any handler runs", async (_case, request) => {
    const response = await app().fetch(request(await mint()));

    expect(response.status).toBe(403);
    expect(response.headers.get("www-authenticate")).toBe(
      `Bearer error="insufficient_scope", error_description="asap:capability_denied", resource_metadata="${METADATA_URL}"`,
    );
    expect(await response.json()).toEqual(DENIED_BODY);
    expect(handlerRuns).toEqual([]);
  });

  it("lets a method listed in allowMethods reach its handler", async () => {
    const response = await app({ allowMethods: ["resources/read"] }).fetch(UNDECLARED_REQUESTS[2]![1](await mint()));

    expect(response.status).toBe(200);
    expect(handlerRuns).toEqual(["resources/read"]);
  });

  it("refuses a token request that does not send MCP-Protocol-Version 2026-07-28", async () => {
    const headers = toolsCallHeaders(PUBLIC_TOOL).slice(1);
    const response = await app().fetch(post([...headers, ...bearer(await mint())], toolsCallBody(PUBLIC_TOOL)));

    expect(response.status).toBe(403);
    expect(handlerRuns).toEqual([]);
  });

  it.each([PUBLIC_TOOL, PROTECTED_TOOL])("still lets the token reach the declared tool %s", async (tool) => {
    const response = await app().fetch(post([...toolsCallHeaders(tool), ...bearer(await mint())], toolsCallBody(tool)));

    expect(response.status).toBe(200);
    // The protected tool's grant check (the second layer) refuses the zero-grant token in its result.
    expect(handlerRuns).toEqual(tool === PUBLIC_TOOL ? [PUBLIC_TOOL] : []);
  });
});

describe("deny by default: what the config declares", () => {
  it("lets a token call only the public tools when no toolCapabilities are set", async () => {
    const request = post([...toolsCallHeaders(PROTECTED_TOOL), ...bearer(await ZERO_GRANT_TOKENS[0]![1]())], toolsCallBody(PROTECTED_TOOL));

    expect((await app({ toolCapabilities: undefined }).fetch(request)).status).toBe(403);
  });

  it.each<[string, Partial<McpAuthConfig>, string]>([
    ["tools/call in allowMethods", { allowMethods: ["tools/call"] }, 'allowMethods cannot list "tools/call"'],
    ["a tool name in the base64 form of an Mcp-Name value", { publicTools: ["=?base64?ZWNobw==?="] }, "has the base64 form"],
  ])("refuses %s at creation", (_case, overrides, message) => {
    expect(() => app(overrides)).toThrow(message);
  });
});

describe("deny by default leaves requests with no token as they were", () => {
  it("runs a public tool with no token", async () => {
    const response = await app().fetch(post(toolsCallHeaders(PUBLIC_TOOL), toolsCallBody(PUBLIC_TOOL)));

    expect(response.status).toBe(200);
    expect(handlerRuns).toEqual([PUBLIC_TOOL]);
  });

  it("answers an allowMethods method with no token with 401: allowMethods needs a verified token", async () => {
    const response = await app({ allowMethods: ["resources/read"] }).fetch(mcpRequest("resources/read", { uri: RESOURCE_URI }, RESOURCE_URI));

    expect(response.status).toBe(401);
    expect(handlerRuns).toEqual([]);
  });
});
