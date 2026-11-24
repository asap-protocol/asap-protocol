import {
  AgentJwtFailureReason,
  InMemoryJtiReplayCache,
  JtiReplayCacheFullError,
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
  /**
   * Called with the detailed reason when a token is refused, for your logs.
   * The caller gets one public reason for an unknown agent, an agent that is
   * not usable, an unsupported key, and a bad signature. It never receives the token.
   */
  readonly onTokenRefused?: (reason: AgentJwtFailureReason) => void;
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
const AGENT_OR_SIGNATURE = "agent or signature not accepted";

/** Told apart, these four reasons tell a caller whether an agent exists and whether it is usable. */
function publicReason(reason: AgentJwtFailureReason): string {
  switch (reason) {
    case AgentJwtFailureReason.UnknownAgent:
    case AgentJwtFailureReason.AgentNotUsable:
    case AgentJwtFailureReason.UnsupportedKey:
    case AgentJwtFailureReason.BadSignature:
      return AGENT_OR_SIGNATURE;
    default:
      return reason;
  }
}

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
 * throws `OAuthError(invalid_token, "asap:invalid_token: <fixed reason>")`,
 * where an unknown agent, an unusable agent, an unsupported key, and a bad
 * signature share one reason and `onTokenRefused` gets the detail; a
 * `JtiReplayCacheFullError` passes through unchanged (the middleware answers
 * 503); any other throw from the resolver or the replay cache becomes a plain
 * `Error` (with the original as `cause`), which the SDK answers with HTTP 500 and no detail. The
 * replay check is always on: each Agent JWT is single-use.
 *
 * @example
 * ```ts
 * const verifier = createAgentJwtVerifier({ audience: "https://mcp.example.com/mcp", resolveAgent });
 * const gate = requireBearerAuth({ verifier }); // from @modelcontextprotocol/server
 * ```
 */
export function createAgentJwtVerifier(config: AgentJwtVerifierConfig): OAuthTokenVerifier {
  const audiences = typeof config.audience === "string" ? [config.audience] : config.audience;
  if (audiences.length === 0 || audiences.some((audience) => audience.trim() === "")) {
    throw new TypeError(
      `agentJwt.audience must be a non-blank string or a non-empty list of them (PRD §7); received ${JSON.stringify(config.audience)}`,
    );
  }
  const replayCache = config.replayCache ?? new InMemoryJtiReplayCache();
  return {
    async verifyAccessToken(token: string): Promise<AuthInfo> {
      let result: AgentJwtVerifyResult;
      try {
        result = await verifyAgentJwt(token, { ...config, replayCache });
      } catch (cause) {
        // A full replay cache is a temporary failure, not a bad token: the middleware answers 503.
        if (cause instanceof JtiReplayCacheFullError) throw cause;
        throw new Error(VERIFIER_FAILURE_MESSAGE, { cause });
      }
      if (!result.ok) {
        try {
          config.onTokenRefused?.(result.reason);
        } catch {
          // A failing log hook must not change the answer.
        }
        throw new OAuthError(OAuthErrorCode.InvalidToken, `${McpAuthErrorCode.InvalidToken}: ${publicReason(result.reason)}`);
      }
      return agentJwtAuthInfo(token, result);
    },
  };
}
