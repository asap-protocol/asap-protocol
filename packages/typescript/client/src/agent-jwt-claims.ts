/** Python `_IAT_MAX_FUTURE_SKEW_SECONDS` (`src/asap/auth/agent_jwt.py`). */
const IAT_MAX_FUTURE_SKEW_SECONDS = 60;

/**
 * Why `verifyAgentJwt` refused a token. Each value is a fixed string with no
 * token content, so a server may return it to the caller. The texts follow the
 * fixed error strings of Python `verify_agent_jwt`.
 */
export const AgentJwtFailureReason = {
  Malformed: "invalid JWT structure",
  WrongTyp: "invalid typ for agent JWT",
  UnsupportedAlg: "alg is not EdDSA or Ed25519",
  MissingSub: "missing sub (agent id)",
  MissingIss: "missing iss (host thumbprint)",
  UnknownAgent: "unknown agent",
  AgentNotUsable: "agent not usable",
  UnsupportedKey: "agent key is not an Ed25519 public JWK",
  BadSignature: "invalid signature",
  Expired: "token expired or missing exp",
  IatInFuture: "invalid iat (too far in the future)",
  MissingJti: "missing jti",
  AudienceMismatch: "audience mismatch",
  Replay: "jti replay detected",
} as const;

export type AgentJwtFailureReason = (typeof AgentJwtFailureReason)[keyof typeof AgentJwtFailureReason];

/** Claims as decoded from the token: untrusted until every check has passed. */
export type AgentJwtClaims = Readonly<Record<string, unknown>>;

export function nonBlankString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() !== "" ? value : undefined;
}

/** Python `audience_matches_expected`: true when `aud` (a string or a list) shares a value with `expected`. */
function audienceMatches(aud: unknown, expected: string | readonly string[]): boolean {
  const accepted = typeof expected === "string" ? [expected] : expected;
  const tokenAudiences = typeof aud === "string" ? [aud] : Array.isArray(aud) ? aud : [];
  return tokenAudiences.some((value) => typeof value === "string" && accepted.includes(value));
}

/**
 * Port of Python `_validate_standard_claims`: `exp`, `iat`, `jti`, then `aud`,
 * and the first failure wins. `exp` is required and exclusive; `iat` is
 * optional and may be up to 60 s ahead. Unlike Python, a numeric string is not
 * a valid `exp` or `iat` (RFC 7519 NumericDate is a JSON number).
 *
 * @example
 * ```ts
 * standardClaimFailure({ exp: now + 60, jti: "jwt_1", aud: "a" }, now, "a"); // undefined
 * ```
 */
export function standardClaimFailure(
  claims: AgentJwtClaims,
  nowSeconds: number,
  audience: string | readonly string[],
): AgentJwtFailureReason | undefined {
  const { exp, iat } = claims;
  if (typeof exp !== "number" || nowSeconds >= exp) {
    return AgentJwtFailureReason.Expired;
  }
  if (iat !== undefined && (typeof iat !== "number" || iat > nowSeconds + IAT_MAX_FUTURE_SKEW_SECONDS)) {
    return AgentJwtFailureReason.IatInFuture;
  }
  if (nonBlankString(claims.jti) === undefined) {
    return AgentJwtFailureReason.MissingJti;
  }
  if (!audienceMatches(claims.aud, audience)) {
    return AgentJwtFailureReason.AudienceMismatch;
  }
  return undefined;
}

/** The string entries of the `capabilities` claim, or `[]` (Python `JwtVerifyResult.capabilities`). */
export function capabilitiesOf(claims: AgentJwtClaims): string[] {
  const raw = claims.capabilities;
  return Array.isArray(raw) ? raw.filter((entry): entry is string => typeof entry === "string") : [];
}
