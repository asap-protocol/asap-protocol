/**
 * A local OAuth 2.1 issuer for the example's external-token path (test only).
 * Its key pair is created at each start and its private key never leaves the
 * process, so only the tests and the compliance mode can mint its tokens. The
 * server gets its public keys as a static JWKS: nothing is fetched.
 */
import { calculateJwkThumbprint, exportJWK, generateKeyPair, SignJWT, type JSONWebKeySet } from "jose";

import { FORBIDDEN_ACTION, PROTECTED_CAPABILITY } from "./demo-identity.js";
import type { ExternalIssuerConfig } from "@asap-protocol/mcp-auth";

/** `.invalid` is reserved (RFC 6761): this issuer name can never be a real host. */
export const TEST_ISSUER = "https://test-issuer.invalid";
/** The scope that the test issuer's grant rule maps to the protected capability. */
export const EXTERNAL_SCOPE = "secure_action:run";
const ACCESS_TOKEN_SECONDS = 300;

export interface TestIssuer {
  readonly jwks: JSONWebKeySet;
  /** The issuer entry for `createMcpAuthMiddleware({ externalTokens: { issuers: [...] } })`. */
  configFor(audience: string): ExternalIssuerConfig;
  /** An RFC 9068 access token for `audience` with this `scope`, valid for five minutes. */
  mint(audience: string, scope: string): Promise<string>;
}

export async function createTestIssuer(): Promise<TestIssuer> {
  const { publicKey, privateKey } = await generateKeyPair("ES256", { extractable: true });
  const publicJwk = await exportJWK(publicKey);
  const kid = await calculateJwkThumbprint(publicJwk);
  const jwks: JSONWebKeySet = { keys: [{ ...publicJwk, kid, alg: "ES256", use: "sig" }] };
  return {
    jwks,
    configFor: (audience) => ({
      issuer: TEST_ISSUER,
      audience,
      jwks,
      grantRules: [{ scope: EXTERNAL_SCOPE, capability: PROTECTED_CAPABILITY, constraints: { action: { not_in: [FORBIDDEN_ACTION] } } }],
    }),
    mint: (audience, scope) =>
      new SignJWT({ scope, client_id: "example-client" })
        .setProtectedHeader({ alg: "ES256", kid, typ: "at+jwt" })
        .setIssuer(TEST_ISSUER)
        .setSubject("example-user")
        .setAudience(audience)
        .setIssuedAt()
        .setExpirationTime(`${ACCESS_TOKEN_SECONDS}s`)
        .sign(privateKey),
  };
}
