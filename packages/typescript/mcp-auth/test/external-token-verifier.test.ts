import { OAuthError } from "@modelcontextprotocol/server";
import type { JWTPayload } from "jose";
import { beforeAll, describe, expect, it } from "vitest";

import { createExternalTokenVerifier, type ExternalIssuerConfig } from "../src/index.js";
import { hmacToken, LocalTestIssuer, unsignedToken } from "./local-issuer.js";
import { MCP_ENDPOINT } from "./mcp-requests.js";

const ISSUER = "https://issuer.example";
const OTHER_ISSUER = "https://other-issuer.example";
const READ_GRANT = { capability: "file.read", constraints: { path: { in: ["/srv/public"] } } };

let issuer: LocalTestIssuer;
let other: LocalTestIssuer;
let impostor: LocalTestIssuer;

beforeAll(async () => {
  [issuer, other, impostor] = await Promise.all([
    LocalTestIssuer.create(ISSUER),
    LocalTestIssuer.create(OTHER_ISSUER, "EdDSA"),
    LocalTestIssuer.create(ISSUER),
  ]);
});

function issuerConfig(overrides: Partial<ExternalIssuerConfig> = {}): ExternalIssuerConfig {
  return { issuer: ISSUER, audience: MCP_ENDPOINT, jwks: issuer.jwks, grantRules: [{ scope: "files:read", ...READ_GRANT }], ...overrides };
}

function verifier(overrides: Partial<ExternalIssuerConfig> = {}) {
  return createExternalTokenVerifier({ issuers: [issuerConfig(overrides), { issuer: OTHER_ISSUER, audience: MCP_ENDPOINT, jwks: other.jwks }] });
}

describe("createExternalTokenVerifier: accepted tokens (COMPAT-001)", () => {
  it("turns a token from an allowlisted issuer for this audience into a principal with the mapped grants", async () => {
    const token = await issuer.mint(issuer.claims(MCP_ENDPOINT, { scope: "files:read admin" }));

    const authInfo = await verifier().verifyAccessToken(token);

    expect(authInfo).toMatchObject({ token, clientId: "client-1", scopes: ["files:read", "admin"] });
    expect(authInfo.expiresAt).toBeGreaterThan(Date.now() / 1000);
    expect(authInfo.extra?.principal).toEqual({
      kind: "external",
      issuer: ISSUER,
      subject: "user-1",
      scopes: ["files:read", "admin"],
      grants: [READ_GRANT],
    });
  });

  it.each<[string, JWTPayload]>([
    ["an unmapped scope", { scope: "files:write" }],
    ["no scope claim", {}],
  ])("gives no grant for %s (deny by default, COMPAT-002)", async (_case, extra) => {
    const authInfo = await verifier().verifyAccessToken(await issuer.mint(issuer.claims(MCP_ENDPOINT, extra)));

    expect(authInfo.extra?.principal).toMatchObject({ grants: [] });
  });

  // An OAuth access token is sent on every request, so there is no replay check.
  it("accepts the same token twice", async () => {
    const auth = verifier();
    const token = await issuer.mint(issuer.claims(MCP_ENDPOINT));

    await expect(auth.verifyAccessToken(token)).resolves.toBeDefined();
    await expect(auth.verifyAccessToken(token)).resolves.toBeDefined();
  });

  it.each<[string, () => Promise<string>, Partial<ExternalIssuerConfig>]>([
    ["an audience list that holds this server", () => issuer.mint(issuer.claims(["https://api.example", MCP_ENDPOINT])), {}],
    ["one of several configured audiences", () => issuer.mint(issuer.claims("https://alias.example/mcp")), { audience: [MCP_ENDPOINT, "https://alias.example/mcp"] }],
    ["an iss with a trailing slash (Python issuer_matches_expected)", () => issuer.mint(issuer.claims(MCP_ENDPOINT, { iss: `${ISSUER}/` })), {}],
    ["a token from the other allowlisted issuer (EdDSA)", () => other.mint(other.claims(MCP_ENDPOINT)), {}],
    ["a token with no typ header", () => issuer.mint(issuer.claims(MCP_ENDPOINT), { typ: undefined }), {}],
    ["a client id from azp", () => issuer.mint(issuer.claims(MCP_ENDPOINT, { client_id: undefined, azp: "client-1" })), {}],
  ])("accepts %s", async (_case, makeToken, overrides) => {
    await expect(verifier(overrides).verifyAccessToken(await makeToken())).resolves.toMatchObject({ clientId: "client-1" });
  });
});

describe("createExternalTokenVerifier: clock, client id, and server-side failures", () => {
  it("uses the subject as the client id when the token has no client_id or azp", async () => {
    const token = await issuer.mint(issuer.claims(MCP_ENDPOINT, { client_id: undefined }));

    await expect(verifier().verifyAccessToken(token)).resolves.toMatchObject({ clientId: "user-1" });
  });

  it("checks exp against the configured clock", async () => {
    const auth = createExternalTokenVerifier({ issuers: [issuerConfig()], nowSeconds: () => Date.now() / 1000 + 600 });

    await expect(auth.verifyAccessToken(await issuer.mint(issuer.claims(MCP_ENDPOINT)))).rejects.toThrow("asap:invalid_token: token expired");
  });

  it("throws a plain error, not a token error, when a configured key cannot be used", async () => {
    const auth = verifier({ jwks: { keys: [{ ...issuer.jwks.keys[0], x: "AAAA" }] } });

    const error = await auth.verifyAccessToken(await issuer.mint(issuer.claims(MCP_ENDPOINT))).catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(Error);
    expect(error).not.toBeInstanceOf(OAuthError);
  });
});

describe("createExternalTokenVerifier: refused tokens get 401 asap:invalid_token with a fixed reason", () => {
  const now = () => Math.floor(Date.now() / 1000);
  const SIGNATURE = "issuer or signature not accepted";

  it.each<[string, () => Promise<string>, string]>([
    ["a string that is not a JWT", async () => "not-a-jwt", "malformed token"],
    ["an ASAP Agent JWT", () => issuer.mint(issuer.claims(MCP_ENDPOINT), { typ: "agent+jwt" }), "token type not accepted"],
    ["an ASAP Host JWT", () => issuer.mint(issuer.claims(MCP_ENDPOINT), { typ: "host+jwt" }), "token type not accepted"],
    ["an Agent JWT typ in another spelling", () => issuer.mint(issuer.claims(MCP_ENDPOINT), { typ: "application/AGENT+JWT" }), "token type not accepted"],
    ["alg none", async () => unsignedToken({ alg: "none", typ: "at+jwt" }, issuer.claims(MCP_ENDPOINT)), "algorithm not accepted"],
    ["an HMAC token", () => hmacToken(issuer.claims(MCP_ENDPOINT)), "algorithm not accepted"],
    ["a signature that is not base64url", async () => `${(await issuer.mint(issuer.claims(MCP_ENDPOINT))).split(".").slice(0, 2).join(".")}.!!`, "malformed token"],
    ["an issuer that is not allowlisted", () => issuer.mint(issuer.claims(MCP_ENDPOINT, { iss: "https://evil.example" })), SIGNATURE],
    ["a token with no iss", () => issuer.mint(issuer.claims(MCP_ENDPOINT, { iss: undefined })), SIGNATURE],
    ["a token signed by another allowlisted issuer that claims this issuer", () => issuer.mint(issuer.claims(MCP_ENDPOINT), { alg: "EdDSA" }, other), SIGNATURE],
    ["a token signed with a key that is not the issuer's", () => issuer.mint(issuer.claims(MCP_ENDPOINT), {}, impostor), SIGNATURE],
    ["a token for another audience", () => issuer.mint(issuer.claims("https://other.example/mcp")), "audience mismatch"],
    ["an expired token", () => issuer.mint(issuer.claims(MCP_ENDPOINT, { exp: now() - 10 })), "token expired"],
    ["a token that is not valid yet", () => issuer.mint(issuer.claims(MCP_ENDPOINT, { nbf: now() + 600 })), "token not yet valid"],
    ["a token with no exp", () => issuer.mint(issuer.claims(MCP_ENDPOINT, { exp: undefined })), "missing or invalid claim"],
    ["a token with no sub", () => issuer.mint(issuer.claims(MCP_ENDPOINT, { sub: undefined })), "missing or invalid claim"],
    ["a token with a blank sub", () => issuer.mint(issuer.claims(MCP_ENDPOINT, { sub: " " })), "missing or invalid claim"],
  ])("refuses %s", async (_case, makeToken, reason) => {
    const token = await makeToken();
    const error = await verifier().verifyAccessToken(token).catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(OAuthError);
    expect(error).toMatchObject({ code: "invalid_token", message: `asap:invalid_token: ${reason}` });
  });
});
