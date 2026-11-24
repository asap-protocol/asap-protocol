import { describe, expect, expectTypeOf, it } from "vitest";

import { MCP_PROTOCOL_VERSION, type OAuthTokenVerifier, type PublicJwk, type ResolvedAgentKey } from "../src/index.js";

describe("@asap-protocol/mcp-auth package entry", () => {
  it("exports the MCP protocol revision that the token layer checks", () => {
    expect(MCP_PROTOCOL_VERSION).toBe("2026-07-28");
  });

  it("re-exports the SDK verifier type for public signatures", async () => {
    const verifier: OAuthTokenVerifier = {
      verifyAccessToken: async (token) => ({ token, clientId: "agent", scopes: [] }),
    };
    await expect(verifier.verifyAccessToken("t")).resolves.toMatchObject({ clientId: "agent" });
  });

  it("re-exports the DOM-free public key type that the resolver returns", () => {
    const key: PublicJwk = { kty: "OKP", crv: "Ed25519", x: "11qYAYKxCrfVS_7TyWQHOg7hcvPapiMlrwIaaPcHURo" };

    expectTypeOf<ResolvedAgentKey["publicKey"]>().toEqualTypeOf<PublicJwk>();
    expect(key.crv).toBe("Ed25519");
  });
});
