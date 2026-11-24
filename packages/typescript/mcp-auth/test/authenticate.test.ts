import {
  createAgent,
  createHost,
  InMemoryJtiReplayCache,
  JtiReplayCacheFullError,
  MemoryStorage,
  type AgentContext,
  type AgentKeyResolver,
} from "@asap-protocol/client";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";

import { createMcpAuthMiddleware, type AgentJwtVerifierConfig, type McpAuthConfig } from "../src/index.js";
import {
  MCP_ENDPOINT,
  NOT_PUBLIC_REQUESTS,
  post,
  PROTECTED_TOOL,
  PUBLIC_REQUESTS,
  PUBLIC_TOOL,
  toolsCallHeaders,
  type HeaderList,
} from "./mcp-requests.js";

const METADATA_URL = "https://mcp.example.com/.well-known/oauth-protected-resource/mcp";

let agent: AgentContext;
let consoleSpies: MockInstance[];

beforeAll(async () => {
  agent = await createAgent(await createHost({ storage: new MemoryStorage() }), { mode: "delegated" });
});

// (d) The package must not log; any console call would be a place a token could leak.
beforeEach(() => {
  consoleSpies = (["log", "info", "warn", "error", "debug"] as const).map((method) =>
    vi.spyOn(console, method).mockImplementation(() => undefined),
  );
});

afterEach(() => {
  for (const spy of consoleSpies) {
    expect(spy).not.toHaveBeenCalled();
  }
  vi.restoreAllMocks();
});

const resolveTestAgent: AgentKeyResolver = async ({ agentId }) =>
  agentId === agent.agentId ? { publicKey: agent.publicJwk, usable: true } : undefined;

function mint(aud = MCP_ENDPOINT): Promise<string> {
  return agent.signAgentJwt({ aud, capabilities: ["web_search"] });
}

function middleware(overrides: Partial<McpAuthConfig> = {}, agentJwt: Partial<AgentJwtVerifierConfig> = {}) {
  return createMcpAuthMiddleware({
    publicTools: [PUBLIC_TOOL],
    toolCapabilities: { [PROTECTED_TOOL]: "web_search" },
    resourceMetadataUrl: METADATA_URL,
    agentJwt: { audience: MCP_ENDPOINT, resolveAgent: resolveTestAgent, ...agentJwt },
    ...overrides,
  });
}

function withToken(headers: HeaderList, token: string): HeaderList {
  return [...headers, ["authorization", `Bearer ${token}`]];
}

async function expect401(result: unknown, description: string, challenge: string): Promise<Response> {
  expect(result).toBeInstanceOf(Response);
  const response = result as Response;
  expect(response.status).toBe(401);
  expect(response.headers.get("www-authenticate")).toBe(challenge);
  expect(response.headers.get("content-type")).toBe("application/json");
  expect(await response.clone().json()).toEqual({ error: "invalid_token", error_description: description });
  return response;
}

function invalidTokenChallenge(description: string): string {
  return `Bearer error="invalid_token", error_description="${description}", resource_metadata="${METADATA_URL}"`;
}

describe("authenticate: requests with no token (header rule)", () => {
  it.each(PUBLIC_REQUESTS)("lets %s pass with no auth info", async (_case, headers) => {
    expect(await middleware().authenticate(post(headers))).toBeUndefined();
  });

  it("sends a plain Bearer challenge when no metadata URL is configured", async () => {
    const result = await middleware({ resourceMetadataUrl: undefined }).authenticate(post(toolsCallHeaders(PROTECTED_TOOL)));

    await expect401(result, "asap:auth_required", "Bearer");
  });

  // (a) Every forged variant (see docs/adapters/mcp-auth-bridge.md), and the other non-public requests, get a 401.
  // With no token, the challenge has no error parameter (RFC 6750 §3.1).
  it.each(NOT_PUBLIC_REQUESTS)("refuses %s with 401 asap:auth_required", async (_case, headers, body) => {
    await expect401(
      await middleware().authenticate(post(headers, body)),
      "asap:auth_required",
      `Bearer resource_metadata="${METADATA_URL}"`,
    );
  });
});

describe("authenticate: requests with a token", () => {
  it.each([PROTECTED_TOOL, PUBLIC_TOOL])("verifies the token for %s and returns AuthInfo", async (tool) => {
    const authInfo = await middleware().authenticate(post(withToken(toolsCallHeaders(tool), await mint())));

    expect(authInfo).toMatchObject({ clientId: agent.agentId, extra: { principal: { agentId: agent.agentId } } });
  });

  // (b) MCP: an invalid token MUST get a 401, so a token on a public tool is never skipped.
  // (f) code[: fixed reason], never claim values or library messages.
  it.each<[string, () => Promise<string>, Partial<AgentJwtVerifierConfig>, string]>([
    ["a string that is not a JWT", async () => "not-a-jwt", {}, "malformed token"],
    ["a token for another audience", () => mint("https://other.example/mcp"), {}, "audience mismatch"],
    ["an expired token", () => mint(), { nowSeconds: () => Date.now() / 1000 + 120 }, "token expired or missing exp"],
    // One public reason, so the answer does not tell whether the agent exists or is usable.
    ["a token from an unknown agent", () => mint(), { resolveAgent: async () => undefined }, "agent or signature not accepted"],
    ["a token from an agent that is not usable", () => mint(), { resolveAgent: async () => ({ publicKey: agent.publicJwk, usable: false }) }, "agent or signature not accepted"],
    ["a resolver key that is not Ed25519", () => mint(), { resolveAgent: async () => ({ publicKey: { kty: "RSA" }, usable: true }) }, "agent or signature not accepted"],
  ])("refuses %s with 401 and a fixed reason, even on a public tool", async (_case, makeToken, agentJwt, reason) => {
    const token = await makeToken();
    const response = await middleware({}, agentJwt).authenticate(post(withToken(toolsCallHeaders(PUBLIC_TOOL), token)));

    const text = `asap:invalid_token: ${reason}`;
    await expectNoTokenIn(await expect401(response, text, invalidTokenChallenge(text)), token);
  });

  // (c) An Agent JWT is single-use.
  it("refuses a replayed Agent JWT", async () => {
    const auth = middleware();
    const token = await mint();
    const request = () => post(withToken(toolsCallHeaders(PROTECTED_TOOL), token));

    await auth.authenticate(request());
    const replay = await auth.authenticate(request());

    const text = "asap:invalid_token: jti replay detected";
    await expectNoTokenIn(await expect401(replay, text, invalidTokenChallenge(text)), token);
  });

  it.each(["Basic dXNlcjpwYXNz", "Bearer", "Bearer two tokens", "Bearer a, Bearer b"])(
    "refuses the Authorization header %j as malformed",
    async (authorization) => {
      const response = await middleware().authenticate(post([...toolsCallHeaders(PUBLIC_TOOL), ["authorization", authorization]]));

      const text = "asap:invalid_token: malformed Authorization header";
      await expect401(response, text, invalidTokenChallenge(text));
    },
  );
});

/** (d) Neither the token nor its signature appears in the body or in any header. */
async function expectNoTokenIn(response: Response, token: string): Promise<void> {
  const exposed = [await response.text()];
  response.headers.forEach((value, name) => exposed.push(`${name}: ${value}`));
  expect(exposed.join("\n")).not.toContain(token);
  expect(exposed.join("\n")).not.toContain(token.split(".")[2] ?? token);
}

// (e) Any verifier failure ends as a 401 or a 500 with no detail, never as a throw.
describe("authenticate: failures that are not about the token", () => {
  const SERVER_ERROR_BODY = { error: "server_error", error_description: "Internal Server Error" };

  it.each<[string, Partial<AgentJwtVerifierConfig>]>([
    ["the resolver throws", { resolveAgent: async () => Promise.reject(new Error("db.internal:5432 refused")) }],
    ["the replay cache throws", { replayCache: { checkAndRecord: () => Promise.reject(new Error("redis down")) } }],
  ])("answers 500 with no detail when %s, and reports the error to onInternalError", async (_case, agentJwt) => {
    const onInternalError = vi.fn();
    const token = await mint();

    const response = await middleware({ onInternalError }, agentJwt).authenticate(post(withToken(toolsCallHeaders(PROTECTED_TOOL), token)));

    expect(response).toBeInstanceOf(Response);
    expect((response as Response).status).toBe(500);
    expect(await (response as Response).json()).toEqual(SERVER_ERROR_BODY);
    expect(onInternalError).toHaveBeenCalledOnce();
    expect(String(onInternalError.mock.calls[0]![0])).not.toContain(token);
  });

  // The cache refuses a new record rather than evict a live one; the token may be fine.
  it("answers 503 with Retry-After when the replay cache is full of live records, and reports the error", async () => {
    const onInternalError = vi.fn();
    const clock = { now: 1_800_000_000 };
    const replayCache = new InMemoryJtiReplayCache({ maxSize: 1, ttlSeconds: 90, nowSeconds: () => clock.now });
    const auth = middleware({ onInternalError }, { replayCache });
    await auth.authenticate(post(withToken(toolsCallHeaders(PROTECTED_TOOL), await mint())));
    clock.now += 30;

    const response = (await auth.authenticate(post(withToken(toolsCallHeaders(PROTECTED_TOOL), await mint())))) as Response;

    expect(response.status).toBe(503);
    expect(response.headers.get("retry-after")).toBe("60");
    expect(response.headers.get("www-authenticate")).toBeNull();
    expect(await response.json()).toEqual({ error: "temporarily_unavailable", error_description: "Authentication service unavailable" });
    expect(onInternalError.mock.calls[0]![0]).toBeInstanceOf(JtiReplayCacheFullError);
  });

  it("still answers 500 when onInternalError itself throws", async () => {
    const auth = middleware(
      { onInternalError: () => { throw new Error("logger down"); } },
      { resolveAgent: async () => Promise.reject(new Error("db down")) },
    );

    const response = await auth.authenticate(post(withToken(toolsCallHeaders(PROTECTED_TOOL), await mint())));

    expect((response as Response).status).toBe(500);
  });

  it("refuses a config with a blank audience at creation (no mode skips the audience)", () => {
    expect(() => middleware({}, { audience: " " })).toThrow("agentJwt.audience");
    expect(() => middleware({}, { audience: [] })).toThrow("agentJwt.audience");
  });
});
