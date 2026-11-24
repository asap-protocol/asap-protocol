import { createAgent, createHost, MemoryStorage, type AgentContext } from "@asap-protocol/client";
import { completable, createMcpHandler, McpServer, type NotificationMethod } from "@modelcontextprotocol/server";
import { beforeAll, beforeEach, describe, expect, it } from "vitest";
import * as z from "zod";

import { createMcpAuthMiddleware, MCP_PROTOCOL_VERSION, type McpAuthConfig, type McpAuthMiddleware } from "../src/index.js";
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
  // Notification handlers are code that no grant covers: deny by default must keep notifications away from them.
  for (const [method] of NOTIFICATIONS) server.server.setNotificationHandler(method, () => void handlerRuns.push(method));
  server.server.fallbackNotificationHandler = async (notification) => void handlerRuns.push(`fallback: ${notification.method}`);
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

const NOTIFICATIONS: [NotificationMethod, Record<string, unknown>][] = [
  ["notifications/cancelled", { requestId: 1 }],
  ["notifications/progress", { progressToken: 1, progress: 1 }],
  ["notifications/roots/list_changed", {}],
  ["notifications/initialized", {}],
];

/** A 2026-07-28 notification (no id). The SDK client sends none with an Mcp-Method header; `name` adds Mcp-Name. */
function notification(method: string, params: Record<string, unknown>, headers: HeaderList, withMethodHeader = true, name?: string): Request {
  const standard: HeaderList = [["mcp-protocol-version", MCP_PROTOCOL_VERSION], ...(withMethodHeader ? [["mcp-method", method] as [string, string]] : [])];
  if (name !== undefined) standard.push(["mcp-name", name]);
  return post([...standard, ...headers], { jsonrpc: "2.0", method, params: { ...params, _meta: { "io.modelcontextprotocol/protocolVersion": MCP_PROTOCOL_VERSION } } });
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

  it.each(NOTIFICATIONS)("refuses the notification %s with HTTP 403 before any handler runs, with or without Mcp-Method", async (method, params) => {
    for (const withMethodHeader of [false, true]) {
      const response = await app().fetch(notification(method, params, bearer(await mint()), withMethodHeader));

      expect(response.status).toBe(403);
    }
    expect(handlerRuns).toEqual([]);
  });

  // A notification may carry the Mcp-Method of a method that the header rule lets through; it must still reach no handler.
  it.each<[string, string, string?]>([
    ["tools/call", "tools/call", PUBLIC_TOOL],
    ["tools/call", "tools/call", PROTECTED_TOOL],
    ["tools/list", "tools/list"],
    ["server/discover", "server/discover"],
  ])("refuses a %s notification with HTTP 403, so it runs no tool and no notification handler", async (_case, method, name) => {
    const response = await app().fetch(notification(method, { name, arguments: {} }, bearer(await mint()), true, name));

    expect(response.status).toBe(403);
    expect(handlerRuns).toEqual([]);
  });

  it("lets a notification listed in allowMethods reach its own handler only, when the client sends its Mcp-Method", async () => {
    const auth = app({ allowMethods: ["notifications/cancelled"] });

    expect((await auth.fetch(notification("notifications/cancelled", { requestId: 1 }, bearer(await mint()), false))).status).toBe(403);
    expect((await auth.fetch(notification("notifications/cancelled", { requestId: 1 }, bearer(await mint())))).status).toBe(202);
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(handlerRuns).toEqual(["notifications/cancelled"]);
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

  it.each<[string, string, string?]>([
    ["a tools/call notification for a public tool", "tools/call", PUBLIC_TOOL],
    ["a tools/list notification", "tools/list"],
    ["a server/discover notification", "server/discover"],
    ["a notification listed in allowMethods", "notifications/cancelled"],
  ])("answers %s with no token with 401, and runs no handler", async (_case, method, name) => {
    const response = await app({ allowMethods: ["notifications/cancelled"] }).fetch(notification(method, { name, arguments: {} }, [], true, name));
    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(response.status).toBe(401);
    expect(handlerRuns).toEqual([]);
  });

  it("answers an allowMethods method with no token with 401: allowMethods needs a verified token", async () => {
    const response = await app({ allowMethods: ["resources/read"] }).fetch(mcpRequest("resources/read", { uri: RESOURCE_URI }, RESOURCE_URI));

    expect(response.status).toBe(401);
    expect(handlerRuns).toEqual([]);
  });
});
