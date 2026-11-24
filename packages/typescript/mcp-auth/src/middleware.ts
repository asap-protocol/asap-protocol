import { bearerAuthChallengeResponse, OAuthError, OAuthErrorCode, type AuthInfo } from "@modelcontextprotocol/server";

import { createAgentJwtVerifier, type AgentJwtVerifierConfig } from "./agent-jwt-verifier.js";
import { McpAuthErrorCode } from "./errors.js";
import { isPublicMcpRequest } from "./public-request.js";

export interface McpAuthConfig {
  /**
   * ASAP Agent JWT verification. With Agent JWTs, this package is a custom
   * auth strategy, which MCP allows; it is not MCP authorization (OAuth 2.1).
   */
  readonly agentJwt: AgentJwtVerifierConfig;
  /** Tools that a request with no token may call, under the header rule. */
  readonly publicTools?: readonly string[];
  /** RFC 9728 metadata URL, sent as `resource_metadata` in every 401 challenge. */
  readonly resourceMetadataUrl?: string;
  /**
   * Called when verification fails for a reason other than the token, such as
   * a resolver that throws. The request gets HTTP 500 with no detail. The
   * package itself never logs, and this hook never receives the token.
   */
  readonly onInternalError?: (error: unknown) => void;
}

export interface McpAuthMiddleware {
  /**
   * The token layer: `AuthInfo` for a verified token, `undefined` for a public
   * request with no token, or the `Response` to return (401, or 500 with no
   * detail). It never throws.
   */
  authenticate(request: Request): Promise<AuthInfo | Response | undefined>;
}

// RFC 6750 §2.1: "Bearer" 1*SP b64token. A token list or a second word does not match.
const BEARER_AUTHORIZATION_PATTERN = /^Bearer +([A-Za-z0-9\-._~+/]+=*)$/i;
const MALFORMED_AUTHORIZATION_REASON = "malformed Authorization header";

/** No token: per RFC 6750 §3.1 the challenge has no `error` parameter; the body still names the ASAP code. */
function authRequiredResponse(resourceMetadataUrl: string | undefined): Response {
  const body = new OAuthError(OAuthErrorCode.InvalidToken, McpAuthErrorCode.AuthRequired).toResponseObject();
  const challenge = resourceMetadataUrl === undefined ? "Bearer" : `Bearer resource_metadata="${resourceMetadataUrl}"`;
  return Response.json(body, { status: 401, headers: { "WWW-Authenticate": challenge } });
}

function invalidTokenResponse(reason: string, resourceMetadataUrl: string | undefined): Response {
  const error = new OAuthError(OAuthErrorCode.InvalidToken, `${McpAuthErrorCode.InvalidToken}: ${reason}`);
  return bearerAuthChallengeResponse(error, { resourceMetadataUrl });
}

/**
 * Create the ASAP auth middleware for an HTTP MCP server on
 * `@modelcontextprotocol/server` 2.x (MCP-TS-001). The token layer runs on the
 * headers, before the SDK parses the body. A request that carries a token is
 * always verified, even for a public tool.
 *
 * @example
 * ```ts
 * const auth = createMcpAuthMiddleware({
 *   agentJwt: { audience: "https://mcp.example.com/mcp", resolveAgent },
 *   publicTools: ["echo"],
 * });
 * const result = await auth.authenticate(request);
 * if (result instanceof Response) return result;
 * ```
 */
export function createMcpAuthMiddleware(config: McpAuthConfig): McpAuthMiddleware {
  const verifier = createAgentJwtVerifier(config.agentJwt);
  const publicTools: ReadonlySet<string> = new Set(config.publicTools);
  // URL#href percent-encodes quotes, backslashes, and spaces, so it is safe inside a quoted-string.
  const resourceMetadataUrl = config.resourceMetadataUrl === undefined ? undefined : new URL(config.resourceMetadataUrl).href;

  function reportInternalError(error: unknown): void {
    try {
      config.onInternalError?.(error);
    } catch {
      // A failing hook must not turn the 500 into an unhandled throw.
    }
  }

  async function authenticate(request: Request): Promise<AuthInfo | Response | undefined> {
    const authorization = request.headers.get("authorization");
    if (authorization === null) {
      return isPublicMcpRequest(request.headers, publicTools) ? undefined : authRequiredResponse(resourceMetadataUrl);
    }
    const token = BEARER_AUTHORIZATION_PATTERN.exec(authorization)?.[1];
    if (token === undefined) {
      return invalidTokenResponse(MALFORMED_AUTHORIZATION_REASON, resourceMetadataUrl);
    }
    try {
      return await verifier.verifyAccessToken(token);
    } catch (error) {
      // The verifier throws OAuthError only with fixed text; anything else is a 500 with no detail.
      if (!(error instanceof OAuthError)) {
        reportInternalError(error);
      }
      return bearerAuthChallengeResponse(error, { resourceMetadataUrl });
    }
  }

  return { authenticate };
}
