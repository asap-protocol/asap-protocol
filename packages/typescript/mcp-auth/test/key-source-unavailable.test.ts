import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { createMcpAuthMiddleware, JwksUnavailableError, type McpAuthConfig } from "../src/index.js";
import { LocalTestIssuer, unsignedToken } from "./local-issuer.js";
import { MCP_ENDPOINT, post, PROTECTED_TOOL, toolsCallHeaders } from "./mcp-requests.js";

const ISSUER = "https://issuer.example";
const JWKS_URI = "https://keys.issuer.example/jwks.json";
// Python parity: the same status and text as OAuth2Middleware's key-source failure.
const UNAVAILABLE_BODY = { error: "temporarily_unavailable", error_description: "Authentication service unavailable" };

let issuer: LocalTestIssuer;
let clockMs: number;
let fetches: number;
let serve: (init: RequestInit) => Promise<Response>;

beforeAll(async () => {
  issuer = await LocalTestIssuer.create(ISSUER);
});

beforeEach(() => {
  clockMs = 1_000_000;
  fetches = 0;
  serve = async () => Response.json(issuer.jwks);
});

const fakeFetch = (async (_input: string | URL | Request, init?: RequestInit) => {
  fetches += 1;
  return serve(init ?? {});
}) as typeof fetch;

function middleware(overrides: Partial<McpAuthConfig> = {}) {
  return createMcpAuthMiddleware({
    externalTokens: {
      issuers: [{ issuer: ISSUER, audience: MCP_ENDPOINT, jwksUri: JWKS_URI }],
      jwksFetch: { fetch: fakeFetch, nowMs: () => clockMs, refreshCooldownMs: 30_000, maxBytes: 1024, timeoutMs: 20 },
    },
    toolCapabilities: { [PROTECTED_TOOL]: PROTECTED_TOOL },
    ...overrides,
  });
}

function authResult(auth: ReturnType<typeof middleware>, token: string) {
  return auth.authenticate(post([...toolsCallHeaders(PROTECTED_TOOL), ["authorization", `Bearer ${token}`]]));
}

async function authenticate(auth: ReturnType<typeof middleware>, token: string): Promise<Response> {
  const result = await authResult(auth, token);
  expect(result).toBeInstanceOf(Response);
  return result as Response;
}

/** 503, the fixed body, Retry-After, and nothing else: no challenge, no host, no token, no library text. */
async function expect503(response: Response, retryAfter: string, token: string): Promise<void> {
  expect(response.status).toBe(503);
  expect(await response.clone().json()).toEqual(UNAVAILABLE_BODY);
  const headers: Record<string, string> = {};
  response.headers.forEach((value, name) => (headers[name] = value));
  expect(headers).toEqual({ "content-type": "application/json", "retry-after": retryAfter });
  const exposed = `${await response.text()}\n${JSON.stringify(headers)}`;
  for (const secret of [token, "issuer.example", "JWKS", "fetch failed"]) expect(exposed).not.toContain(secret);
}

describe("a key source that cannot be reached is HTTP 503 (parity with the Python external-token path)", () => {
  it.each<[string, (init: RequestInit) => Promise<Response>]>([
    ["a network error", async () => Promise.reject(new TypeError("fetch failed"))],
    ["a timeout", async (init) => new Promise((_resolve, reject) => init.signal?.addEventListener("abort", () => reject(init.signal?.reason)))],
    ["an HTTP error", async () => new Response("down", { status: 502 })],
    ["an oversize document", async () => new Response(JSON.stringify({ keys: [], pad: "x".repeat(2048) }))],
    ["a refused redirect", async () => new Response(null, { status: 302, headers: { location: "https://169.254.169.254/" } })],
    ["a body that is not a JWKS", async () => Response.json({ keys: "none" })],
  ])("answers %s with 503 and Retry-After until the next allowed fetch", async (_case, response) => {
    serve = response;
    const onInternalError = vi.fn();
    const token = await issuer.mint(issuer.claims(MCP_ENDPOINT));

    await expect503(await authenticate(middleware({ onInternalError }), token), "30", token);
    expect(onInternalError).toHaveBeenCalledOnce();
    expect(onInternalError.mock.calls[0]![0]).toBeInstanceOf(JwksUnavailableError);
    expect(String(onInternalError.mock.calls[0]![0])).not.toContain(token);
  });

  it("answers 503 within the cooldown after a failed fetch, without fetching, with the time left", async () => {
    serve = async () => new Response("down", { status: 503 });
    const auth = middleware();
    const token = await issuer.mint(issuer.claims(MCP_ENDPOINT));
    await authenticate(auth, token);

    clockMs += 10_500;

    await expect503(await authenticate(auth, token), "20", token);
    expect(fetches).toBe(1);
  });

  it("keeps an unknown kid after a successful refresh a 401: the token is bad, the service is fine", async () => {
    const auth = middleware();
    expect(await authResult(auth, await issuer.mint(issuer.claims(MCP_ENDPOINT)))).not.toBeInstanceOf(Response);
    clockMs += 30_000;

    const response = await authenticate(auth, await issuer.mint(issuer.claims(MCP_ENDPOINT), { kid: "unknown" }));

    expect(fetches).toBe(2);
    expect(response.status).toBe(401);
    expect(await response.json()).toMatchObject({ error: "invalid_token" });
  });

  it("keeps every other internal failure a 500 with no detail", async () => {
    const agent = { audience: MCP_ENDPOINT, resolveAgent: async () => Promise.reject(new Error("db down")) };
    const auth = middleware({ agentJwt: agent, externalTokens: undefined });

    const response = await authenticate(auth, unsignedToken({ alg: "EdDSA", typ: "agent+jwt" }, { sub: "agent", iss: "host" }));

    expect(response.status).toBe(500);
    expect(await response.json()).toEqual({ error: "server_error", error_description: "Internal Server Error" });
  });
});
