import { SignJWT } from "jose";
import { ulid } from "ulid";

import { generateEd25519KeyPair } from "./ed25519-keypair.js";
import type { Storage } from "./storage-local.js";

/** Matches Python `HOST_JWT_TYP` / `AGENT_JWT_TYP` (`src/asap/auth/agent_jwt.py`). */
export const HOST_JWT_TYP = "host+jwt" as const;
export const AGENT_JWT_TYP = "agent+jwt" as const;

const HOST_PUBLIC_KEY_CLAIM = "host_public_key" as const;
const AGENT_PUBLIC_KEY_CLAIM = "agent_public_key" as const;
const CAPABILITIES_CLAIM = "capabilities" as const;

const DEFAULT_HOST_JWT_TTL_SECONDS = 300;
/** Python `AGENT_JWT_TTL_SECONDS`: the lifetime of a minted Agent JWT. */
export const AGENT_JWT_TTL_SECONDS = 60;

export type AgentMode = "delegated" | "autonomous";

/** Host JWT `iss` — RFC 7638 SHA-256 thumbprint of the host public OKP JWK (Python `jwk_thumbprint_sha256`). */
export async function jwkThumbprintSha256(publicKey: Record<string, unknown>): Promise<string> {
  const kty = publicKey.kty;
  let required: Record<string, unknown>;
  if (kty === "OKP") {
    required = {
      crv: publicKey.crv,
      kty,
      x: publicKey.x,
    };
  } else {
    throw new Error(`unsupported kty for thumbprint: ${String(kty)}`);
  }
  const canonical = JSON.stringify(required, ["crv", "kty", "x"]);
  const bytes = new TextEncoder().encode(canonical);
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
  return b64url(digest);
}

function b64url(data: Uint8Array): string {
  let bin = "";
  for (let i = 0; i < data.length; i += 1) {
    bin += String.fromCharCode(data[i]!);
  }
  const b64 = btoa(bin);
  return b64.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/u, "");
}

function hostPrivateJwkKey(hostId: string): string {
  return `asap/v2/host/${hostId}/ed25519-private.jwk.json`;
}

function agentPrivateJwkKey(hostId: string, agentId: string): string {
  return `asap/v2/host/${hostId}/agent/${agentId}/ed25519-private.jwk.json`;
}

async function exportPrivateJwk(key: CryptoKey): Promise<JsonWebKey> {
  return crypto.subtle.exportKey("jwk", key) as Promise<JsonWebKey>;
}

async function exportPublicJwk(key: CryptoKey): Promise<JsonWebKey> {
  return crypto.subtle.exportKey("jwk", key) as Promise<JsonWebKey>;
}

function publicJwkForClaims(jwk: JsonWebKey): Record<string, string> {
  if (jwk.kty !== "OKP" || jwk.crv !== "Ed25519" || typeof jwk.x !== "string") {
    throw new Error("Expected Ed25519 OKP public JWK");
  }
  return { crv: "Ed25519", kty: "OKP", x: jwk.x };
}

function stripPrivateJwk(jwk: JsonWebKey): JsonWebKey {
  const rest = { ...jwk };
  delete rest.d;
  return rest;
}

type Ed25519PrivateJwk = JsonWebKey & { d?: string };

async function importEd25519PrivateFromJwk(jwk: JsonWebKey): Promise<CryptoKey> {
  return crypto.subtle.importKey(
    "jwk",
    jwk,
    { name: "Ed25519" },
    false,
    ["sign"],
  ) as Promise<CryptoKey>;
}

/**
 * A public JSON Web Key (RFC 7517), such as an agent's or a host's Ed25519 key
 * (`{ kty: "OKP", crv: "Ed25519", x }`). It needs no DOM lib, so server code
 * compiled without one keeps real key types. `JsonWebKey` from the DOM lib and
 * jose's `JWK` are assignable to it, and it is assignable to `JsonWebKey`.
 */
export interface PublicJwk {
  kty?: string;
  crv?: string;
  x?: string;
  y?: string;
  n?: string;
  e?: string;
  alg?: string;
  kid?: string;
  use?: string;
  /** Web Crypto exports set these two. */
  ext?: boolean;
  key_ops?: string[];
  /** The private part: never set on a public key. Declared so that a check for its absence compiles. */
  d?: string;
}

/** Host runtime identity. */
export interface HostContext {
  readonly hostId: string;
  readonly hostThumbprint: string;
  readonly publicJwk: PublicJwk;
  readonly storage: Storage;
  signHostJwt(options: {
    aud: string | string[];
    agentPublicKey?: PublicJwk;
    ttlSeconds?: number;
  }): Promise<string>;
}

export interface AgentContext {
  readonly agentId: string;
  readonly hostId: string;
  readonly mode: AgentMode;
  readonly hostThumbprint: string;
  readonly publicJwk: PublicJwk;
  readonly storage: Storage;
  signAgentJwt(options: { aud: string | string[]; capabilities?: string[] }): Promise<string>;
}

/**
 * Create a new host identity (Ed25519) and persist the private JWK in `storage`.
 *
 * Uses Web Crypto when available and falls back to `@noble/ed25519` when key generation is unavailable.
 */
export async function createHost(opts: { storage: Storage }): Promise<HostContext> {
  const pair = await generateEd25519KeyPair();
  const privateJwk = await exportPrivateJwk(pair.privateKey);
  const publicJwk = await exportPublicJwk(pair.publicKey);
  const hostThumbprint = await jwkThumbprintSha256(publicJwkForClaims(publicJwk));
  const hostId = `host_${crypto.randomUUID()}`;

  await opts.storage.set(hostPrivateJwkKey(hostId), JSON.stringify(privateJwk));

  const publicOnly = stripPrivateJwk(publicJwk);

  return {
    hostId,
    hostThumbprint,
    publicJwk: publicOnly,
    storage: opts.storage,
    signHostJwt: async (options) =>
      signHostJwtInternal({
        storage: opts.storage,
        hostId,
        hostThumbprint,
        hostPublicJwk: publicOnly,
        ...options,
      }),
  };
}

async function signHostJwtInternal(input: {
  storage: Storage;
  hostId: string;
  hostThumbprint: string;
  hostPublicJwk: JsonWebKey;
  aud: string | string[];
  agentPublicKey?: JsonWebKey;
  ttlSeconds?: number;
}): Promise<string> {
  const raw = await input.storage.get(hostPrivateJwkKey(input.hostId));
  if (raw === undefined) {
    throw new Error(`missing host private key for ${input.hostId}`);
  }
  const privateJwk = JSON.parse(raw) as Ed25519PrivateJwk;
  const signKey = await importEd25519PrivateFromJwk(privateJwk);
  privateJwk.d = undefined;

  const now = Math.floor(Date.now() / 1000);
  const ttl = input.ttlSeconds ?? DEFAULT_HOST_JWT_TTL_SECONDS;
  const jti = `jwt_${ulid()}`;
  const hostPubClaim = publicJwkForClaims(input.hostPublicJwk);

  const body: Record<string, unknown> = {
    [HOST_PUBLIC_KEY_CLAIM]: hostPubClaim,
  };
  if (input.agentPublicKey !== undefined) {
    body[AGENT_PUBLIC_KEY_CLAIM] = publicJwkForClaims(stripPrivateJwk(input.agentPublicKey));
  }

  const builder = new SignJWT(body)
    .setProtectedHeader({ alg: "EdDSA", typ: HOST_JWT_TYP })
    .setIssuer(input.hostThumbprint)
    .setAudience(input.aud)
    .setIssuedAt(now)
    .setExpirationTime(now + ttl)
    .setJti(jti);

  return builder.sign(signKey);
}

/**
 * Create a new agent Ed25519 identity under a host and persist the private JWK.
 */
export async function createAgent(
  host: HostContext,
  opts: { mode: AgentMode },
): Promise<AgentContext> {
  const pair = await generateEd25519KeyPair();
  const privateJwk = await exportPrivateJwk(pair.privateKey);
  const publicJwk = stripPrivateJwk(await exportPublicJwk(pair.publicKey));
  const agentId = `agent_${crypto.randomUUID()}`;

  await host.storage.set(agentPrivateJwkKey(host.hostId, agentId), JSON.stringify(privateJwk));

  return {
    agentId,
    hostId: host.hostId,
    mode: opts.mode,
    hostThumbprint: host.hostThumbprint,
    publicJwk,
    storage: host.storage,
    signAgentJwt: async (options) =>
      signAgentJwtInternal({
        storage: host.storage,
        hostId: host.hostId,
        agentId,
        hostThumbprint: host.hostThumbprint,
        agentPublicJwk: publicJwk,
        ...options,
      }),
  };
}

/**
 * Reloads a {@link HostContext} from persisted Ed25519 material in `storage` (browser LocalStorage, etc.).
 */
export async function resumeHost(opts: { storage: Storage; hostId: string }): Promise<HostContext> {
  const raw = await opts.storage.get(hostPrivateJwkKey(opts.hostId));
  if (raw === undefined) {
    throw new Error(`resumeHost: missing private key for host ${opts.hostId}`);
  }
  const privateJwk = JSON.parse(raw) as JsonWebKey;
  const publicOnly = stripPrivateJwk(privateJwk);
  const hostThumbprint = await jwkThumbprintSha256(publicJwkForClaims(publicOnly));

  return {
    hostId: opts.hostId,
    hostThumbprint,
    publicJwk: publicOnly,
    storage: opts.storage,
    signHostJwt: async (options) =>
      signHostJwtInternal({
        storage: opts.storage,
        hostId: opts.hostId,
        hostThumbprint,
        hostPublicJwk: publicOnly,
        ...options,
      }),
  };
}

/**
 * Reloads an {@link AgentContext} from persisted material under the given host.
 */
export async function resumeAgent(host: HostContext, opts: { agentId: string; mode: AgentMode }): Promise<AgentContext> {
  const raw = await host.storage.get(agentPrivateJwkKey(host.hostId, opts.agentId));
  if (raw === undefined) {
    throw new Error(`resumeAgent: missing private key for agent ${opts.agentId}`);
  }
  const privateJwk = JSON.parse(raw) as JsonWebKey;
  const publicJwk = stripPrivateJwk(privateJwk);

  return {
    agentId: opts.agentId,
    hostId: host.hostId,
    mode: opts.mode,
    hostThumbprint: host.hostThumbprint,
    publicJwk,
    storage: host.storage,
    signAgentJwt: async (options) =>
      signAgentJwtInternal({
        storage: host.storage,
        hostId: host.hostId,
        agentId: opts.agentId,
        hostThumbprint: host.hostThumbprint,
        agentPublicJwk: publicJwk,
        ...options,
      }),
  };
}

async function signAgentJwtInternal(input: {
  storage: Storage;
  hostId: string;
  agentId: string;
  hostThumbprint: string;
  agentPublicJwk: JsonWebKey;
  aud: string | string[];
  capabilities?: string[];
}): Promise<string> {
  const raw = await input.storage.get(agentPrivateJwkKey(input.hostId, input.agentId));
  if (raw === undefined) {
    throw new Error(`missing agent private key for ${input.agentId}`);
  }
  const privateJwk = JSON.parse(raw) as Ed25519PrivateJwk;
  const signKey = await importEd25519PrivateFromJwk(privateJwk);
  privateJwk.d = undefined;

  const now = Math.floor(Date.now() / 1000);
  const jti = `jwt_${ulid()}`;

  const body: Record<string, unknown> = {};
  if (input.capabilities !== undefined) {
    body[CAPABILITIES_CLAIM] = input.capabilities;
  }

  const builder = new SignJWT(body)
    .setProtectedHeader({ alg: "EdDSA", typ: AGENT_JWT_TYP })
    .setIssuer(input.hostThumbprint)
    .setSubject(input.agentId)
    .setAudience(input.aud)
    .setIssuedAt(now)
    .setExpirationTime(now + AGENT_JWT_TTL_SECONDS)
    .setJti(jti);

  return builder.sign(signKey);
}
