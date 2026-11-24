import { createAgent, createHost, MemoryStorage, type AgentContext, type AgentKeyResolver } from "@asap-protocol/client";
import { OAuthError, OAuthErrorCode, requireBearerAuth } from "@modelcontextprotocol/server";
import { beforeAll, describe, expect, it, vi } from "vitest";

import { createAgentJwtVerifier, type AgentJwtVerifierConfig } from "../src/index.js";

const AUDIENCE = "https://mcp.example.com/mcp";
const AGENT_JWT_TTL_SECONDS = 60;

let agent: AgentContext;
let otherAgent: AgentContext;

beforeAll(async () => {
  agent = await createAgent(await createHost({ storage: new MemoryStorage() }), { mode: "delegated" });
  otherAgent = await createAgent(await createHost({ storage: new MemoryStorage() }), { mode: "delegated" });
});

/** The minting hook: one fresh, single-use Agent JWT per request. */
function mint(aud = AUDIENCE): Promise<string> {
  return agent.signAgentJwt({ aud, capabilities: ["web_search"] });
}

const resolveTestAgent: AgentKeyResolver = async ({ agentId }) =>
  agentId === agent.agentId ? { publicKey: agent.publicJwk, usable: true } : undefined;

function verifierWith(overrides: Partial<AgentJwtVerifierConfig> = {}) {
  return createAgentJwtVerifier({ audience: AUDIENCE, resolveAgent: resolveTestAgent, ...overrides });
}

/** The rejection, which must be an OAuthError with a fixed text and no token content. */
async function rejectionOf(promise: Promise<unknown>, token: string): Promise<OAuthError> {
  const error = await promise.then(
    () => expect.unreachable("the verifier accepted the token"),
    (thrown: unknown) => thrown,
  );
  expect(error).toBeInstanceOf(OAuthError);
  const oauthError = error as OAuthError;
  expect(oauthError.code).toBe(OAuthErrorCode.InvalidToken);
  expect(oauthError.message).not.toContain(token);
  expect(oauthError.message).not.toContain(token.split(".")[2]);
  return oauthError;
}

describe("createAgentJwtVerifier (OAuthTokenVerifier)", () => {
  it("returns AuthInfo with the agent as principal in extra", async () => {
    const token = await mint();

    const authInfo = await verifierWith().verifyAccessToken(token);

    expect(authInfo).toEqual({
      token,
      clientId: agent.agentId,
      scopes: [],
      expiresAt: expect.any(Number),
      extra: {
        principal: {
          kind: "agent_jwt",
          agentId: agent.agentId,
          hostThumbprint: agent.hostThumbprint,
          capabilities: ["web_search"],
        },
      },
    });
    expect(authInfo.expiresAt! - Date.now() / 1000).toBeLessThanOrEqual(AGENT_JWT_TTL_SECONDS);
  });

  it.each([
    ["an expired token", { nowSeconds: () => Date.now() / 1000 + 2 * AGENT_JWT_TTL_SECONDS }, AUDIENCE, "token expired or missing exp"],
    ["a token for another audience", {}, "https://other.example.com/mcp", "audience mismatch"],
  ])("refuses %s with asap:invalid_token and a fixed reason", async (_case, overrides, aud, reason) => {
    const token = await mint(aud);

    const error = await rejectionOf(verifierWith(overrides).verifyAccessToken(token), token);

    expect(error.message).toBe(`asap:invalid_token: ${reason}`);
  });

  // Told apart, these four tell a caller whether an agent exists and whether it is usable.
  it.each<[string, AgentKeyResolver, string]>([
    ["an unknown agent", async () => undefined, "unknown agent"],
    ["an agent that is not usable", async () => ({ publicKey: agent.publicJwk, usable: false }), "agent not usable"],
    ["a key that is not Ed25519", async () => ({ publicKey: { kty: "RSA" }, usable: true }), "agent key is not an Ed25519 public JWK"],
    ["a bad signature", async () => ({ publicKey: otherAgent.publicJwk, usable: true }), "invalid signature"],
  ])("gives %s one public reason, and the detailed reason to onTokenRefused only", async (_case, resolveAgent, detail) => {
    const onTokenRefused = vi.fn();
    const token = await mint();

    const error = await rejectionOf(verifierWith({ resolveAgent, onTokenRefused }).verifyAccessToken(token), token);

    expect(error.message).toBe("asap:invalid_token: agent or signature not accepted");
    expect(onTokenRefused).toHaveBeenCalledExactlyOnceWith(detail);
  });

  it("still refuses the token when onTokenRefused throws", async () => {
    const onTokenRefused = () => {
      throw new Error("logger down");
    };
    const token = await mint();

    const error = await rejectionOf(verifierWith({ resolveAgent: async () => undefined, onTokenRefused }).verifyAccessToken(token), token);

    expect(error.message).toBe("asap:invalid_token: agent or signature not accepted");
  });

  it("refuses a string that is not a JWT", async () => {
    const error = await rejectionOf(verifierWith().verifyAccessToken("opaque-access-token"), "opaque-access-token");

    expect(error.message).toBe("asap:invalid_token: invalid JWT structure");
  });

  it("refuses a replayed token with no cache configured (replay detection is on by default)", async () => {
    const verifier = verifierWith();
    const token = await mint();

    await verifier.verifyAccessToken(token);
    const error = await rejectionOf(verifier.verifyAccessToken(token), token);

    expect(error.message).toBe("asap:invalid_token: jti replay detected");
  });

  it.each([
    ["an Error", new Error("store down at db.internal:5432")],
    ["an OAuthError", new OAuthError(OAuthErrorCode.InvalidToken, "resolver text")],
  ])("turns %s from the resolver into a plain Error, so the SDK answers 500 without detail", async (_case, thrown) => {
    const token = await mint();
    const verifier = verifierWith({ resolveAgent: async () => Promise.reject(thrown) });

    const error = await verifier.verifyAccessToken(token).catch((caught: unknown) => caught);

    expect(error).not.toBeInstanceOf(OAuthError);
    expect((error as Error).message).toBe("Agent JWT verification failed: the agent resolver or the replay cache threw");
    expect((error as Error).cause).toBe(thrown);
  });

  it("plugs into the SDK's requireBearerAuth", async () => {
    const gate = requireBearerAuth({ verifier: verifierWith() });
    const valid = new Request(AUDIENCE, { headers: { authorization: `Bearer ${await mint()}` } });
    const wrongAudience = new Request(AUDIENCE, { headers: { authorization: `Bearer ${await mint("https://other.example")}` } });

    const accepted = await gate(valid);
    const refused = await gate(wrongAudience);

    expect(accepted).toMatchObject({ clientId: agent.agentId });
    expect(refused).toBeInstanceOf(Response);
    expect((refused as Response).status).toBe(401);
    expect(await (refused as Response).json()).toEqual({
      error: "invalid_token",
      error_description: "asap:invalid_token: audience mismatch",
    });
  });
});
