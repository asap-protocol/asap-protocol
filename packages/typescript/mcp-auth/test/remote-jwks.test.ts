import { OAuthError } from "@modelcontextprotocol/server";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { createExternalTokenVerifier, type RemoteJwksOptions } from "../src/index.js";
import { LocalTestIssuer } from "./local-issuer.js";
import { MCP_ENDPOINT } from "./mcp-requests.js";

const ISSUER = "https://issuer.example";
const JWKS_URI = "https://issuer.example/.well-known/jwks.json";

let issuer: LocalTestIssuer;
let clockMs: number;
let fetches: string[];
let serve: (url: string, init: RequestInit) => Promise<Response>;

beforeAll(async () => {
  issuer = await LocalTestIssuer.create(ISSUER);
});

beforeEach(() => {
  clockMs = 1_000_000;
  fetches = [];
  serve = async () => Response.json(issuer.jwks);
});

const fakeFetch = (async (input: string | URL | Request, init?: RequestInit) => {
  fetches.push(String(input));
  return serve(String(input), init ?? {});
}) as typeof fetch;

function verifier(options: Partial<RemoteJwksOptions> = {}, jwksUri = JWKS_URI) {
  const jwksFetch: RemoteJwksOptions = { fetch: fakeFetch, nowMs: () => clockMs, refreshCooldownMs: 30_000, maxAgeMs: 600_000, ...options };
  return createExternalTokenVerifier({ issuers: [{ issuer: ISSUER, audience: MCP_ENDPOINT, jwksUri }], jwksFetch });
}

const token = () => issuer.mint(issuer.claims(MCP_ENDPOINT));

/** A key-source failure is not a token failure: a plain Error, which the middleware answers with 500. */
async function expectKeySourceFailure(promise: Promise<unknown>): Promise<void> {
  const error = await promise.catch((caught: unknown) => caught);
  expect(error).toBeInstanceOf(Error);
  expect(error).not.toBeInstanceOf(OAuthError);
  expect(String(error)).toContain("JWKS");
}

describe("jwksUri: fetch, cache, and refresh", () => {
  it("fetches the JWKS once and serves later tokens from the cache", async () => {
    const auth = verifier();

    await auth.verifyAccessToken(await token());
    await auth.verifyAccessToken(await token());

    expect(fetches).toEqual([JWKS_URI]);
  });

  it("fetches again once the cache is older than maxAgeMs", async () => {
    const auth = verifier();
    await auth.verifyAccessToken(await token());

    clockMs += 600_001;
    await auth.verifyAccessToken(await token());

    expect(fetches).toHaveLength(2);
  });

  it("drops symmetric and private keys from a fetched JWKS", async () => {
    serve = async () => Response.json({ keys: [{ kty: "oct", k: "c2VjcmV0" }, { ...issuer.jwks.keys[0], d: "cHJpdmF0ZQ" }] });

    await expect(verifier().verifyAccessToken(await token())).rejects.toThrow("asap:invalid_token: issuer or signature not accepted");
  });
});

describe("jwksUri: defaults", () => {
  it("uses the global fetch and the default limits when jwksFetch is not set", async () => {
    vi.stubGlobal("fetch", fakeFetch);
    try {
      const auth = createExternalTokenVerifier({ issuers: [{ issuer: ISSUER, audience: MCP_ENDPOINT, jwksUri: JWKS_URI }] });

      await expect(auth.verifyAccessToken(await token())).resolves.toBeDefined();
      expect(fetches).toEqual([JWKS_URI]);
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

describe("jwksUri: what the fetch refuses", () => {
  it.each(["http://issuer.example/jwks.json", "https://user:secret@issuer.example/jwks.json", "https://issuer.example/jwks.json#keys", "not a url"])(
    "refuses the jwksUri %s at creation",
    (jwksUri) => {
      expect(() => verifier({}, jwksUri)).toThrow(/^jwksUri/);
    },
  );

  it("follows a redirect on the same origin only", async () => {
    serve = async (url) => (url === JWKS_URI ? new Response(null, { status: 302, headers: { location: "/keys/v2" } }) : Response.json(issuer.jwks));

    await verifier().verifyAccessToken(await token());

    expect(fetches).toEqual([JWKS_URI, "https://issuer.example/keys/v2"]);
  });

  it.each<[string, (url: string, init: RequestInit) => Promise<Response>]>([
    ["a redirect to another host", async () => new Response(null, { status: 307, headers: { location: "https://169.254.169.254/latest" } })],
    ["a redirect to http on the same host", async () => new Response(null, { status: 301, headers: { location: "http://issuer.example/jwks.json" } })],
    ["a redirect with no location", async () => new Response(null, { status: 302 })],
    ["endless redirects", async () => new Response(null, { status: 302, headers: { location: "/again" } })],
    ["an HTTP error", async () => new Response("down", { status: 503 })],
    ["a body that is not JSON", async () => new Response("<html>")],
    ["JSON that is not a JWKS", async () => Response.json({ keys: "none" })],
    ["a declared length over maxBytes", async () => new Response("{}", { headers: { "content-length": "4096" } })],
    ["a streamed body over maxBytes", async () => new Response(JSON.stringify({ keys: [], pad: "x".repeat(2048) }))],
    ["a network error", async () => Promise.reject(new TypeError("fetch failed"))],
    ["a response slower than timeoutMs", async (_url, init) => new Promise((_resolve, reject) => init.signal?.addEventListener("abort", () => reject(init.signal?.reason)))],
  ])("fails as a key-source error on %s", async (_case, response) => {
    serve = response;

    await expectKeySourceFailure(verifier({ maxBytes: 1024, timeoutMs: 20 }).verifyAccessToken(await token()));
  });

  it("does not fetch again within the cooldown after a failed fetch", async () => {
    serve = async () => new Response("down", { status: 503 });
    const auth = verifier();
    await expectKeySourceFailure(auth.verifyAccessToken(await token()));

    await expectKeySourceFailure(auth.verifyAccessToken(await token()));
    clockMs += 30_000;
    serve = async () => Response.json(issuer.jwks);

    await expect(auth.verifyAccessToken(await token())).resolves.toBeDefined();
    expect(fetches).toHaveLength(2);
  });
});
