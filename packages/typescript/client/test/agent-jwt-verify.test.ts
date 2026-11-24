import { readFileSync } from "node:fs";

import { exportJWK, generateKeyPair, SignJWT, type JWTPayload } from "jose";
import { describe, expect, it } from "vitest";

import {
  AgentJwtFailureReason,
  InMemoryJtiReplayCache,
  verifyAgentJwt,
  type AgentKeyResolver,
  type VerifyAgentJwtOptions,
} from "../src/index.js";

interface AgentJwtVector {
  now: number;
  audience: string;
  agent_id: string;
  host_thumbprint: string;
  jti: string;
  capabilities: string[];
  agent_public_jwk: JsonWebKey;
  token: string;
}

// Minted by Python create_agent_jwt; tests/auth/test_agent_jwt_interop_vector.py proves it.
const VECTOR_URL = new URL("../../../../tests/fixtures/interop/agent-jwt.json", import.meta.url);
const vector = JSON.parse(readFileSync(VECTOR_URL, "utf8")) as AgentJwtVector;
const AGENT_JWT_TTL_SECONDS = 60;

function resolveTo(publicKey: JsonWebKey, usable = true): AgentKeyResolver {
  return async () => ({ publicKey, usable });
}

function vectorOptions(overrides: Partial<VerifyAgentJwtOptions> = {}): VerifyAgentJwtOptions {
  return {
    audience: vector.audience,
    resolveAgent: resolveTo(vector.agent_public_jwk),
    nowSeconds: () => vector.now,
    ...overrides,
  };
}

function flipSignature(token: string): string {
  const [header, payload, signature] = token.split(".");
  return `${header}.${payload}.${signature!.startsWith("A") ? "B" : "A"}${signature!.slice(1)}`;
}

describe("verifyAgentJwt with the Python vector", () => {
  it("accepts the token that Python minted, at the same clock", async () => {
    const result = await verifyAgentJwt(vector.token, vectorOptions());

    expect(result).toEqual({
      ok: true,
      agentId: vector.agent_id,
      hostThumbprint: vector.host_thumbprint,
      expiresAt: vector.now + AGENT_JWT_TTL_SECONDS,
      capabilities: vector.capabilities,
      claims: {
        iss: vector.host_thumbprint,
        sub: vector.agent_id,
        aud: vector.audience,
        iat: vector.now,
        exp: vector.now + AGENT_JWT_TTL_SECONDS,
        jti: vector.jti,
        capabilities: vector.capabilities,
      },
    });
  });

  it("asks the resolver for the agent and host named in the token", async () => {
    const lookups: unknown[] = [];
    const resolveAgent: AgentKeyResolver = async (lookup) => {
      lookups.push(lookup);
      return { publicKey: vector.agent_public_jwk, usable: true };
    };

    await verifyAgentJwt(vector.token, vectorOptions({ resolveAgent }));

    expect(lookups).toEqual([{ agentId: vector.agent_id, hostThumbprint: vector.host_thumbprint }]);
  });

  it.each([
    ["expired (exp is exclusive)", vectorOptions({ nowSeconds: () => vector.now + AGENT_JWT_TTL_SECONDS }), AgentJwtFailureReason.Expired],
    ["for another audience", vectorOptions({ audience: ["https://other.example.com/mcp"] }), AgentJwtFailureReason.AudienceMismatch],
    ["from an unknown agent", vectorOptions({ resolveAgent: async () => undefined }), AgentJwtFailureReason.UnknownAgent],
    ["from an unusable agent", vectorOptions({ resolveAgent: resolveTo(vector.agent_public_jwk, false) }), AgentJwtFailureReason.AgentNotUsable],
    ["with a key that is not Ed25519", vectorOptions({ resolveAgent: resolveTo({ kty: "EC", crv: "P-256", x: "x", y: "y" }) }), AgentJwtFailureReason.UnsupportedKey],
  ])("rejects the token %s", async (_case, options, reason) => {
    expect(await verifyAgentJwt(vector.token, options)).toEqual({ ok: false, reason });
  });

  it("rejects a token whose signature was changed", async () => {
    const result = await verifyAgentJwt(flipSignature(vector.token), vectorOptions());

    expect(result).toEqual({ ok: false, reason: AgentJwtFailureReason.BadSignature });
  });

  it("rejects the second use of the same token", async () => {
    const replayCache = new InMemoryJtiReplayCache();

    const first = await verifyAgentJwt(vector.token, vectorOptions({ replayCache }));
    const second = await verifyAgentJwt(vector.token, vectorOptions({ replayCache }));

    expect(first.ok).toBe(true);
    expect(second).toEqual({ ok: false, reason: AgentJwtFailureReason.Replay });
  });
});

const { privateKey, publicKey } = await generateKeyPair("Ed25519");
const publicJwk = (await exportJWK(publicKey)) as JsonWebKey;

describe("verifyAgentJwt with tokens signed in the test", () => {
  const NOW = 1_800_000_000;
  const AUDIENCE = "https://mcp.example.com/mcp";
  const validClaims: JWTPayload = { iss: "host-tp", sub: "agent-1", aud: AUDIENCE, iat: NOW, exp: NOW + 60, jti: "jwt_1" };

  function sign(claims: JWTPayload, header: { alg: string; typ?: string } = { alg: "EdDSA", typ: "agent+jwt" }) {
    return new SignJWT(claims).setProtectedHeader(header).sign(privateKey);
  }

  function verify(token: string) {
    return verifyAgentJwt(token, { audience: AUDIENCE, resolveAgent: resolveTo(publicJwk), nowSeconds: () => NOW });
  }

  function unsigned(header: object, claims: JWTPayload): string {
    const encode = (part: object) => Buffer.from(JSON.stringify(part)).toString("base64url");
    return `${encode(header)}.${encode(claims)}.`;
  }

  it("accepts the RFC 9864 alg name Ed25519", async () => {
    const result = await verify(await sign(validClaims, { alg: "Ed25519", typ: "agent+jwt" }));

    expect(result.ok).toBe(true);
  });

  it.each([
    ["a Host JWT", () => sign(validClaims, { alg: "EdDSA", typ: "host+jwt" }), AgentJwtFailureReason.WrongTyp],
    ["no typ", () => sign(validClaims, { alg: "EdDSA" }), AgentJwtFailureReason.WrongTyp],
    ["alg none", async () => unsigned({ alg: "none", typ: "agent+jwt" }, validClaims), AgentJwtFailureReason.UnsupportedAlg],
    ["alg HS256", async () => unsigned({ alg: "HS256", typ: "agent+jwt" }, validClaims), AgentJwtFailureReason.UnsupportedAlg],
    ["no sub", () => sign({ ...validClaims, sub: undefined }), AgentJwtFailureReason.MissingSub],
    ["a blank iss", () => sign({ ...validClaims, iss: " " }), AgentJwtFailureReason.MissingIss],
    ["no jti (claim checks run after the signature)", () => sign({ ...validClaims, jti: undefined }), AgentJwtFailureReason.MissingJti],
    ["three segments that are not JSON", async () => "not.a.jwt", AgentJwtFailureReason.Malformed],
    ["an empty string", async () => "", AgentJwtFailureReason.Malformed],
  ])("rejects %s", async (_case, makeToken, reason) => {
    expect(await verify(await makeToken())).toEqual({ ok: false, reason });
  });
});
