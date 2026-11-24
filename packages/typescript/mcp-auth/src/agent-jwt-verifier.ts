import {
  InMemoryJtiReplayCache,
  verifyAgentJwt,
  type AgentJwtVerifyResult,
  type AgentKeyResolver,
  type JtiReplayCache,
  type VerifiedAgentJwt,
} from "@asap-protocol/client";
import { OAuthError, OAuthErrorCode, type AuthInfo, type OAuthTokenVerifier } from "@modelcontextprotocol/server";

import { McpAuthErrorCode } from "./errors.js";

export interface AgentJwtVerifierConfig {
  /** Required: the token's `aud` must contain one of these values, for example the MCP endpoint URL. */
  readonly audience: string | readonly string[];
  /** Returns the agent's Ed25519 public JWK and whether it is usable (see `verifyAgentJwt`). */
  readonly resolveAgent: AgentKeyResolver;
  /** Default: one in-memory cache per verifier. Share a store when several instances serve one audience. */
  readonly replayCache?: JtiReplayCache;
  /** Clock in seconds since the epoch, for tests. Default: the system clock. */
  readonly nowSeconds?: () => number;
}

/** The ASAP principal that `AuthInfo.extra.principal` carries after an Agent JWT verifies. */
export interface AgentJwtPrincipal {
  readonly kind: "agent_jwt";
  readonly agentId: string;
  readonly hostThumbprint: string;
  /** The `capabilities` claim. The grant layer checks it in the tool result, not as OAuth scopes. */
  readonly capabilities: readonly string[];
}

/** Fixed text, so a store failure never reaches the caller with its own detail. */
const VERIFIER_FAILURE_MESSAGE = "Agent JWT verification failed: the agent resolver or the replay cache threw";

function agentJwtAuthInfo(token: string, verified: VerifiedAgentJwt): AuthInfo {
  const principal: AgentJwtPrincipal = {
    kind: "agent_jwt",
    agentId: verified.agentId,
    hostThumbprint: verified.hostThumbprint,
    capabilities: verified.capabilities,
  };
  // scopes stays empty: Agent JWT grants are not OAuth scopes, and an SDK
  // scope challenge must not treat capabilities as scopes.
  return { token, clientId: verified.agentId, scopes: [], expiresAt: verified.expiresAt, extra: { principal } };
}

/**
 * The ASAP Agent JWT verifier as an MCP SDK `OAuthTokenVerifier`. A bad token
 * throws `OAuthError(invalid_token, "asap:invalid_token: <fixed reason>")`; a
 * throw from the resolver or the replay cache becomes a plain `Error` (with the
 * original as `cause`), which the SDK answers with HTTP 500 and no detail. The
 * replay check is always on: each Agent JWT is single-use.
 *
 * @example
 * ```ts
 * const verifier = createAgentJwtVerifier({ audience: "https://mcp.example.com/mcp", resolveAgent });
 * const gate = requireBearerAuth({ verifier }); // from @modelcontextprotocol/server
 * ```
 */
export function createAgentJwtVerifier(config: AgentJwtVerifierConfig): OAuthTokenVerifier {
  const replayCache = config.replayCache ?? new InMemoryJtiReplayCache();
  return {
    async verifyAccessToken(token: string): Promise<AuthInfo> {
      let result: AgentJwtVerifyResult;
      try {
        result = await verifyAgentJwt(token, { ...config, replayCache });
      } catch (cause) {
        throw new Error(VERIFIER_FAILURE_MESSAGE, { cause });
      }
      if (!result.ok) {
        throw new OAuthError(OAuthErrorCode.InvalidToken, `${McpAuthErrorCode.InvalidToken}: ${result.reason}`);
      }
      return agentJwtAuthInfo(token, result);
    },
  };
}
