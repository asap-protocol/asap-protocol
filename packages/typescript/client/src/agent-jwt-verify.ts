import { compactVerify, decodeJwt, decodeProtectedHeader, importJWK } from "jose";

import {
  AgentJwtFailureReason,
  capabilitiesOf,
  nonBlankString,
  standardClaimFailure,
  type AgentJwtClaims,
} from "./agent-jwt-claims.js";
import { AGENT_JWT_TYP, type PublicJwk } from "./identity.js";
import type { JtiReplayCache } from "./jti-replay-cache.js";

/** `EdDSA` (RFC 8037) and its RFC 9864 name `Ed25519`, as Python `JWT_ALGS_VERIFY`. */
const AGENT_JWT_ALGS = ["EdDSA", "Ed25519"];

/** What the resolver is asked: the unverified `sub` and `iss` of the token. */
export interface AgentKeyLookup {
  readonly agentId: string;
  readonly hostThumbprint: string;
}

/** The agent's Ed25519 public JWK, and whether the agent may authenticate now. */
export interface ResolvedAgentKey {
  readonly publicKey: PublicJwk;
  /** `false` when the agent is revoked, expired, or pending, its host is revoked, or it is not bound to that host. */
  readonly usable: boolean;
}

/**
 * Returns `undefined` for an unknown agent. A throw is an infrastructure failure and propagates.
 * It runs before the signature, audience, and expiry checks, with the token's unverified
 * `sub` and `iss`: keep it a read-only lookup, and update state (such as a last-used time)
 * only after `verifyAgentJwt` returns `ok: true`.
 */
export type AgentKeyResolver = (lookup: AgentKeyLookup) => Promise<ResolvedAgentKey | undefined>;

export interface VerifyAgentJwtOptions {
  /** Required. The token's `aud` must contain one of these values. */
  readonly audience: string | readonly string[];
  readonly resolveAgent: AgentKeyResolver;
  /** Without a cache, a replayed token is not detected. */
  readonly replayCache?: JtiReplayCache;
  /** Clock in seconds since the epoch. Default: the system clock. */
  readonly nowSeconds?: () => number;
}

export interface VerifiedAgentJwt {
  readonly ok: true;
  readonly agentId: string;
  readonly hostThumbprint: string;
  /** `exp`, in seconds since the epoch. */
  readonly expiresAt: number;
  /** The string entries of the `capabilities` claim; empty when it is absent. */
  readonly capabilities: readonly string[];
  readonly claims: AgentJwtClaims;
}

export interface RejectedAgentJwt {
  readonly ok: false;
  readonly reason: AgentJwtFailureReason;
}

export type AgentJwtVerifyResult = VerifiedAgentJwt | RejectedAgentJwt;

function reject(reason: AgentJwtFailureReason): RejectedAgentJwt {
  return { ok: false, reason };
}

function decodeUnverified(token: string): { alg: unknown; typ: unknown; claims: AgentJwtClaims } | undefined {
  try {
    const { alg, typ } = decodeProtectedHeader(token);
    return { alg, typ, claims: decodeJwt(token) };
  } catch {
    return undefined;
  }
}

function ed25519X(jwk: PublicJwk): string | undefined {
  return jwk.kty === "OKP" && jwk.crv === "Ed25519" ? nonBlankString(jwk.x) : undefined;
}

async function signatureIsValid(token: string, x: string): Promise<boolean> {
  try {
    const key = await importJWK({ kty: "OKP", crv: "Ed25519", x }, "EdDSA");
    await compactVerify(token, key, { algorithms: AGENT_JWT_ALGS });
    return true;
  } catch {
    return false;
  }
}

/**
 * Verify an ASAP Agent JWT (`typ: agent+jwt`, Ed25519) with the token checks of
 * Python `verify_agent_jwt`. The agent's status, host revocation, and host
 * binding come from `resolveAgent` here, not from stores. A bad token never
 * throws; a throw from `resolveAgent` or `replayCache` propagates.
 *
 * @example
 * ```ts
 * const result = await verifyAgentJwt(token, {
 *   audience: "https://mcp.example.com/mcp",
 *   resolveAgent: async ({ agentId }) => agents.get(agentId),
 *   replayCache: new InMemoryJtiReplayCache(),
 * });
 * if (!result.ok) console.warn(result.reason);
 * ```
 */
export async function verifyAgentJwt(token: string, options: VerifyAgentJwtOptions): Promise<AgentJwtVerifyResult> {
  const decoded = decodeUnverified(token);
  if (decoded === undefined) return reject(AgentJwtFailureReason.Malformed);
  if (decoded.typ !== AGENT_JWT_TYP) return reject(AgentJwtFailureReason.WrongTyp);
  if (typeof decoded.alg !== "string" || !AGENT_JWT_ALGS.includes(decoded.alg)) {
    return reject(AgentJwtFailureReason.UnsupportedAlg);
  }
  const { claims } = decoded;
  const agentId = nonBlankString(claims.sub);
  if (agentId === undefined) return reject(AgentJwtFailureReason.MissingSub);
  const hostThumbprint = nonBlankString(claims.iss);
  if (hostThumbprint === undefined) return reject(AgentJwtFailureReason.MissingIss);

  const resolved = await options.resolveAgent({ agentId, hostThumbprint });
  if (resolved === undefined) return reject(AgentJwtFailureReason.UnknownAgent);
  if (!resolved.usable) return reject(AgentJwtFailureReason.AgentNotUsable);
  const x = ed25519X(resolved.publicKey);
  if (x === undefined) return reject(AgentJwtFailureReason.UnsupportedKey);
  // The signature covers the payload decoded above, so once it verifies,
  // `claims` and the sub and iss given to the resolver are authentic.
  if (!(await signatureIsValid(token, x))) return reject(AgentJwtFailureReason.BadSignature);

  const nowSeconds = options.nowSeconds?.() ?? Date.now() / 1000;
  const failure = standardClaimFailure(claims, nowSeconds, options.audience);
  if (failure !== undefined) return reject(failure);
  const jti = claims.jti as string;
  const expiresAt = claims.exp as number;
  if (options.replayCache !== undefined && !(await options.replayCache.checkAndRecord(agentId, jti, expiresAt))) {
    return reject(AgentJwtFailureReason.Replay);
  }
  return { ok: true, agentId, hostThumbprint, expiresAt, capabilities: capabilitiesOf(claims), claims };
}
