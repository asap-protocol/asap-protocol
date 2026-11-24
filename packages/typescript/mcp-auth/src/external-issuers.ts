import { createLocalJWKSet, type JSONWebKeySet, type JWK } from "jose";

import { assertValidGrantRules, type ExternalGrantRule } from "./capability-grants.js";
import { createRemoteJwksSource, type RemoteJwksOptions } from "./remote-jwks.js";

/** One allowlisted issuer of external OAuth 2.1 access tokens. */
export interface ExternalIssuerConfig {
  /** The token's `iss`, compared exactly except for trailing slashes (Python `issuer_matches_expected`). */
  readonly issuer: string;
  /** Required (RFC 8707): the token's `aud` must hold one of these values, such as the MCP endpoint URL. */
  readonly audience: string | readonly string[];
  /** The issuer's public keys, set in the config. Only RSA, EC, and OKP public keys are accepted. Set this or `jwksUri`. */
  readonly jwks?: JSONWebKeySet;
  /** An https URL of the issuer's JWKS, fetched as `jwksFetch` says. Other keys in the document are dropped. Set this or `jwks`. */
  readonly jwksUri?: string;
  /** Scope or claim → capability rules. No rule, no grant. */
  readonly grantRules?: readonly ExternalGrantRule[];
}

export interface ExternalTokenConfig {
  /** The allowlist. There is no mode that accepts any issuer. */
  readonly issuers: readonly ExternalIssuerConfig[];
  /** Clock in seconds since the epoch, for tests. Default: the system clock. */
  readonly nowSeconds?: () => number;
  /** Fetch, size, timeout, and cache settings for every `jwksUri`. */
  readonly jwksFetch?: RemoteJwksOptions;
}

/** Where the signature keys of one issuer come from; the `getKey` function that `jose` `jwtVerify` takes. */
export type ExternalKeySource = ReturnType<typeof createLocalJWKSet>;

/** An issuer after its config passed the checks. */
export interface TrustedIssuer {
  readonly config: ExternalIssuerConfig;
  readonly audiences: readonly string[];
  readonly keys: ExternalKeySource;
}

const PUBLIC_KEY_TYPES = ["RSA", "EC", "OKP"];
const PRIVATE_KEY_MEMBERS = ["d", "p", "q", "dp", "dq", "qi", "k"];

/** The allowlist key of an issuer: trailing slashes do not count (Python `iss.rstrip("/")`). */
export function issuerKey(issuer: string): string {
  return issuer.replace(/\/+$/, "");
}

function configError(issuer: string, problem: string): TypeError {
  return new TypeError(`externalTokens issuer ${JSON.stringify(issuer)}: ${problem}`);
}

/** Only RSA, EC, and OKP public keys; a symmetric or private key is a config error (Python `static_jwks` check). */
export function isPublicSignatureKey(key: JWK): boolean {
  return PUBLIC_KEY_TYPES.includes(String(key.kty)) && !PRIVATE_KEY_MEMBERS.some((member) => member in key);
}

function staticKeySource(issuer: string, jwks: JSONWebKeySet): ExternalKeySource {
  const keys: readonly JWK[] = Array.isArray(jwks.keys) ? jwks.keys : [];
  if (keys.length === 0) throw configError(issuer, "needs a jwks with at least one key");
  const unsafe = keys.find((key) => !isPublicSignatureKey(key));
  if (unsafe !== undefined) throw configError(issuer, `jwks key kid=${JSON.stringify(unsafe.kid)} is not an RSA, EC, or OKP public key`);
  return createLocalJWKSet({ keys: [...keys] });
}

function keySource(config: ExternalIssuerConfig, jwksFetch: RemoteJwksOptions | undefined): ExternalKeySource {
  const { jwks, jwksUri } = config;
  if (jwks !== undefined && jwksUri === undefined) return staticKeySource(config.issuer, jwks);
  if (jwks === undefined && jwksUri !== undefined) return createRemoteJwksSource(jwksUri, isPublicSignatureKey, jwksFetch);
  throw configError(config.issuer, "needs exactly one key source: jwks or jwksUri");
}

function trustedIssuer(config: ExternalIssuerConfig, jwksFetch: RemoteJwksOptions | undefined): TrustedIssuer {
  const audiences = typeof config.audience === "string" ? [config.audience] : [...config.audience];
  if (issuerKey(config.issuer).trim() === "") throw configError(config.issuer, "needs a non-blank issuer");
  if (audiences.length === 0 || audiences.some((audience) => audience.trim() === "")) {
    throw configError(config.issuer, `audience must be a non-blank string or a non-empty list of them (PRD §7); received ${JSON.stringify(config.audience)}`);
  }
  assertValidGrantRules(config.grantRules ?? []);
  return { config, audiences, keys: keySource(config, jwksFetch) };
}

/**
 * Check the external-token config once, at creation, and index the issuers by
 * `issuerKey`. Throws a `TypeError` that names the issuer for: an empty
 * allowlist, a blank issuer or audience, an issuer listed twice, no keys, a
 * key that is not an asymmetric public key, or a malformed grant rule.
 */
export function trustedIssuers(config: ExternalTokenConfig): ReadonlyMap<string, TrustedIssuer> {
  if (config.issuers.length === 0) throw new TypeError("externalTokens.issuers must list at least one issuer; there is no accept-any-issuer mode");
  const byKey = new Map<string, TrustedIssuer>();
  for (const issuer of config.issuers) {
    const key = issuerKey(issuer.issuer);
    if (byKey.has(key)) throw configError(issuer.issuer, "is listed twice");
    byKey.set(key, trustedIssuer(issuer, config.jwksFetch));
  }
  return byKey;
}
