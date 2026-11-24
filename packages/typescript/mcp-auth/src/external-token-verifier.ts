import { OAuthError, OAuthErrorCode, type AuthInfo, type OAuthTokenVerifier } from "@modelcontextprotocol/server";
import { decodeJwt, decodeProtectedHeader, errors, jwtVerify, type JWTPayload } from "jose";

import { mapExternalGrants, type CapabilityGrant } from "./capability-grants.js";
import { McpAuthErrorCode } from "./errors.js";
import { issuerKey, trustedIssuers, type ExternalTokenConfig, type TrustedIssuer } from "./external-issuers.js";

/** The ephemeral principal of an external token, keyed by `(issuer, subject)`; `AuthInfo.extra.principal`. */
export interface ExternalPrincipal {
  readonly kind: "external";
  /** The allowlisted issuer, as configured. */
  readonly issuer: string;
  readonly subject: string;
  readonly scopes: readonly string[];
  readonly grants: readonly CapabilityGrant[];
}

/** Python `_ALLOWED_JWT_ALGORITHMS` (RFC 8725 §3.2): asymmetric only, so `none` and HMAC fail before any key lookup. */
const EXTERNAL_TOKEN_ALGORITHMS = ["EdDSA", "RS256", "ES256"];
/** An ASAP token never passes as an external token. */
const ASAP_TOKEN_TYPES = ["agent+jwt", "host+jwt"];
// One text for both, so the answer does not tell which issuers are allowlisted.
const ISSUER_OR_SIGNATURE = "issuer or signature not accepted";
const CLAIM_REASONS: Readonly<Record<string, string>> = { aud: "audience mismatch", nbf: "token not yet valid", iss: ISSUER_OR_SIGNATURE };
const JOSE_REASONS: Readonly<Record<string, string>> = {
  ERR_JWT_EXPIRED: "token expired",
  ERR_JOSE_ALG_NOT_ALLOWED: "algorithm not accepted",
  ERR_JWS_SIGNATURE_VERIFICATION_FAILED: ISSUER_OR_SIGNATURE,
  ERR_JWKS_NO_MATCHING_KEY: ISSUER_OR_SIGNATURE,
  ERR_JWKS_MULTIPLE_MATCHING_KEYS: ISSUER_OR_SIGNATURE,
};

function invalidToken(reason: string): OAuthError {
  return new OAuthError(OAuthErrorCode.InvalidToken, `${McpAuthErrorCode.InvalidToken}: ${reason}`);
}

/** The checks that need no key: token type, algorithm, then the allowlisted issuer. */
function precheck(token: string, issuers: ReadonlyMap<string, TrustedIssuer>): { issuer: TrustedIssuer; iss: string } {
  let header: ReturnType<typeof decodeProtectedHeader>;
  let claims: JWTPayload;
  try {
    header = decodeProtectedHeader(token);
    claims = decodeJwt(token);
  } catch {
    throw invalidToken("malformed token");
  }
  const typ = typeof header.typ === "string" ? header.typ.toLowerCase().replace(/^application\//, "") : undefined;
  if (typ !== undefined && ASAP_TOKEN_TYPES.includes(typ)) throw invalidToken("token type not accepted");
  if (typeof header.alg !== "string" || !EXTERNAL_TOKEN_ALGORITHMS.includes(header.alg)) throw invalidToken("algorithm not accepted");
  const issuer = typeof claims.iss === "string" ? issuers.get(issuerKey(claims.iss)) : undefined;
  if (issuer === undefined) throw invalidToken(ISSUER_OR_SIGNATURE);
  return { issuer, iss: claims.iss as string };
}

function reasonFor(error: InstanceType<typeof errors.JOSEError>): string {
  if (error instanceof errors.JWTClaimValidationFailed) return CLAIM_REASONS[error.claim] ?? "missing or invalid claim";
  return JOSE_REASONS[error.code] ?? "malformed token";
}

function externalAuthInfo(token: string, issuer: TrustedIssuer, payload: JWTPayload): AuthInfo {
  const subject = payload.sub as string;
  const scopes = typeof payload.scope === "string" ? payload.scope.split(" ").filter((scope) => scope !== "") : [];
  const principal: ExternalPrincipal = {
    kind: "external",
    issuer: issuer.config.issuer,
    subject,
    scopes,
    grants: mapExternalGrants(payload, issuer.config.grantRules ?? []),
  };
  const clientId = [payload.client_id, payload.azp].find((value): value is string => typeof value === "string") ?? subject;
  return { token, clientId, scopes, expiresAt: payload.exp, extra: { principal } };
}

/**
 * The external OAuth 2.1 access-token verifier as an MCP SDK
 * `OAuthTokenVerifier` (COMPAT-001). With it, the server is the
 * resource server of MCP authorization. The token picks nothing: the
 * unverified `iss` selects an allowlisted issuer, and the signature is checked
 * with that issuer's keys only; then `aud`, `exp`, and `nbf`. `sub` and `exp`
 * are required. An ASAP Agent or Host JWT, `alg: none`, and HMAC are refused
 * before any key lookup. There is no replay check. A bad
 * token throws `OAuthError(invalid_token, "asap:invalid_token: <fixed reason>")`.
 * A `jwksUri` that cannot be fetched throws `JwksUnavailableError`, which the
 * middleware answers with 503; any other failure is a plain `Error` (500).
 *
 * @example
 * ```ts
 * const verifier = createExternalTokenVerifier({
 *   issuers: [{ issuer: "https://auth.example.com", audience: "https://mcp.example.com/mcp", jwks, grantRules }],
 * });
 * ```
 */
export function createExternalTokenVerifier(config: ExternalTokenConfig): OAuthTokenVerifier {
  const issuers = trustedIssuers(config);
  return {
    async verifyAccessToken(token: string): Promise<AuthInfo> {
      const { issuer, iss } = precheck(token, issuers);
      let payload: JWTPayload;
      try {
        const currentDate = config.nowSeconds === undefined ? undefined : new Date(config.nowSeconds() * 1000);
        const options = { algorithms: EXTERNAL_TOKEN_ALGORITHMS, issuer: iss, audience: [...issuer.audiences], requiredClaims: ["exp", "sub"], currentDate };
        ({ payload } = await jwtVerify(token, issuer.keys, options));
      } catch (error) {
        if (error instanceof errors.JOSEError) throw invalidToken(reasonFor(error));
        throw error;
      }
      if (!Number.isFinite(payload.exp) || (payload.nbf !== undefined && !Number.isFinite(payload.nbf))) {
        throw invalidToken("missing or invalid claim");
      }
      if (typeof payload.sub !== "string" || payload.sub.trim() === "") throw invalidToken("missing or invalid claim");
      return externalAuthInfo(token, issuer, payload);
    },
  };
}
