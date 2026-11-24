import type { JSONWebKeySet } from "jose";
import { beforeAll, describe, expect, it } from "vitest";

import { trustedIssuers } from "../src/external-issuers.js";
import type { ExternalIssuerConfig, ExternalTokenConfig } from "../src/index.js";
import { LocalTestIssuer } from "./local-issuer.js";
import { MCP_ENDPOINT } from "./mcp-requests.js";

const ISSUER = "https://issuer.example";

let issuer: LocalTestIssuer;

beforeAll(async () => {
  issuer = await LocalTestIssuer.create(ISSUER);
});

function issuerConfig(overrides: Partial<ExternalIssuerConfig> = {}): ExternalIssuerConfig {
  return { issuer: ISSUER, audience: MCP_ENDPOINT, jwks: issuer.jwks, grantRules: [{ scope: "files:read", capability: "file.read" }], ...overrides };
}

describe("trustedIssuers: the external-token config is checked at creation", () => {
  it("indexes the allowlist by issuer, ignoring trailing slashes", () => {
    const issuers = trustedIssuers({ issuers: [issuerConfig({ issuer: `${ISSUER}/` }), issuerConfig({ issuer: "https://b.example", audience: ["a", "b"], grantRules: undefined })] });

    expect([...issuers.keys()]).toEqual([ISSUER, "https://b.example"]);
    expect(issuers.get("https://b.example")?.audiences).toEqual(["a", "b"]);
  });

  // Thunks: the table is built before beforeAll creates the issuer.
  it.each<[string, () => ExternalTokenConfig]>([
    ["no issuers (no accept-any-issuer mode)", () => ({ issuers: [] })],
    ["a blank audience", () => ({ issuers: [issuerConfig({ audience: " " })] })],
    ["an empty audience list", () => ({ issuers: [issuerConfig({ audience: [] })] })],
    ["a blank issuer", () => ({ issuers: [issuerConfig({ issuer: "/" })] })],
    ["one issuer twice", () => ({ issuers: [issuerConfig(), issuerConfig({ issuer: `${ISSUER}/` })] })],
    ["no key source", () => ({ issuers: [issuerConfig({ jwks: undefined })] })],
    ["two key sources", () => ({ issuers: [issuerConfig({ jwksUri: "https://issuer.example/jwks.json" })] })],
    ["an empty JWKS", () => ({ issuers: [issuerConfig({ jwks: { keys: [] } })] })],
    ["a JWKS with no keys list", () => ({ issuers: [issuerConfig({ jwks: {} as JSONWebKeySet })] })],
    ["a symmetric key in the JWKS", () => ({ issuers: [issuerConfig({ jwks: { keys: [{ kty: "oct", k: "c2VjcmV0" }] } })] })],
    ["a private key in the JWKS", () => ({ issuers: [issuerConfig({ jwks: { keys: [{ ...issuer.jwks.keys[0], d: "cHJpdmF0ZQ" }] } })] })],
    ["a malformed grant rule", () => ({ issuers: [issuerConfig({ grantRules: [{ scope: "a b", capability: "x" }] })] })],
  ])("refuses %s", (_case, config) => {
    expect(() => trustedIssuers(config())).toThrow(/^externalTokens|^grant rule/);
  });
});
