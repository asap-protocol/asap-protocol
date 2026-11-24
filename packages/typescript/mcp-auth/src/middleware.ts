import { AGENT_JWT_TYP } from "@asap-protocol/client";
import {
  bearerAuthChallengeResponse,
  OAuthError,
  OAuthErrorCode,
  type AuthInfo,
  type McpHandlerRequestOptions,
  type OAuthTokenVerifier,
  type ScopeChallengeHandler,
} from "@modelcontextprotocol/server";
import { decodeProtectedHeader } from "jose";

import { createAgentJwtVerifier, type AgentJwtVerifierConfig } from "./agent-jwt-verifier.js";
import { McpAuthErrorCode } from "./errors.js";
import type { ExternalTokenConfig } from "./external-issuers.js";
import { createExternalTokenVerifier } from "./external-token-verifier.js";
import { createGrantLayer, type AgentGrantResolver, type ProtectTool } from "./grant-layer.js";
import { isPublicMcpRequest } from "./public-request.js";

/** Set `agentJwt`, `externalTokens`, or both. */
export interface McpAuthConfig {
  /**
   * ASAP Agent JWT verification. With Agent JWTs, this package is a custom
   * auth strategy, which MCP allows; it is not MCP authorization (OAuth 2.1).
   */
  readonly agentJwt?: AgentJwtVerifierConfig;
  /**
   * External OAuth 2.1 JWT access tokens from allowlisted issuers. With them,
   * the server is the resource server of MCP authorization (OAuth 2.1).
   */
  readonly externalTokens?: ExternalTokenConfig;
  /** Tools that a request with no token may call, under the header rule. */
  readonly publicTools?: readonly string[];
  /** Tool name → ASAP capability for `protectTool`. A tool that is not listed uses its own name. */
  readonly toolCapabilities?: Readonly<Record<string, string>>;
  /** The grants of a verified Agent JWT principal, for example `staticAgentGrants(...)`. Default: none (deny). */
  readonly agentGrants?: AgentGrantResolver;
  /** RFC 9728 metadata URL: `resource_metadata` in every 401 challenge, and in `AuthInfo` for the SDK's 403 challenge. */
  readonly resourceMetadataUrl?: string;
  /**
   * Called when verification or a grant lookup fails for a reason other than
   * the token, such as a resolver that throws. The request gets HTTP 500 with no detail. The
   * package itself never logs, and this hook never receives the token.
   */
  readonly onInternalError?: (error: unknown) => void;
}

/** The `{ fetch }` face of `createMcpHandler(...)` from `@modelcontextprotocol/server`. */
export interface McpFetchHandler {
  fetch(request: Request, options?: McpHandlerRequestOptions): Promise<Response>;
}

/** The shape that `toNodeHandler` (`@modelcontextprotocol/node`) and fetch-native runtimes accept. */
export interface McpProtectedHandler {
  fetch(request: Request): Promise<Response>;
}

export interface McpAuthMiddleware {
  /**
   * The token layer: `AuthInfo` for a verified token, `undefined` for a public
   * request with no token, or the `Response` to return (401, or 500 with no
   * detail). It never throws.
   */
  authenticate(request: Request): Promise<AuthInfo | Response | undefined>;
  /**
   * Put the token layer in front of an MCP handler. SDK 2.4 has no request
   * middleware, so the layer wraps `handler.fetch`: a refused request gets its
   * `Response`, and any other request reaches `handler.fetch(request, { authInfo })`.
   * Tools read the result as `ctx.http?.authInfo`.
   */
  protect(handler: McpFetchHandler): McpProtectedHandler;
  /**
   * The grant layer: wrap a tool callback, as the third argument of
   * `registerTool`. A call runs only when the principal in
   * `ctx.http.authInfo` holds a grant for the tool's capability and the call
   * arguments meet its constraints; otherwise the result is a tool error
   * (`asap:capability_denied` or `asap:constraint_violation`). A protected
   * tool with no verified principal gets `asap:auth_required`. A public tool
   * runs unchanged.
   */
  protectTool: ProtectTool;
  /**
   * The external-token 403: pass it as `scopeChallenge` to `registerTool` for
   * each protected tool. When an external principal has no grant for the
   * tool's capability and a scope rule of its issuer gives one, the SDK
   * answers HTTP 403 `insufficient_scope` with that scope and
   * `asap:capability_denied`, before the tool runs.
   */
  scopeChallenge: ScopeChallengeHandler;
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

function unverifiedTyp(token: string): unknown {
  try {
    return decodeProtectedHeader(token).typ;
  } catch {
    return undefined;
  }
}

/**
 * One verifier per token: with both token types, the unverified
 * `typ` picks the verifier once, as Python does on the capability route; a
 * token that this verifier refuses is never tried with the other one.
 */
function tokenVerifierRouter(agent?: OAuthTokenVerifier, external?: OAuthTokenVerifier): (token: string) => OAuthTokenVerifier {
  if (agent !== undefined && external !== undefined) return (token) => (unverifiedTyp(token) === AGENT_JWT_TYP ? agent : external);
  const only = agent ?? external;
  if (only === undefined) throw new TypeError("createMcpAuthMiddleware needs agentJwt, externalTokens, or both");
  return () => only;
}

/**
 * Create the ASAP auth middleware for an HTTP MCP server on
 * `@modelcontextprotocol/server` 2.x (MCP-TS-001). The token layer runs on the
 * headers, before the SDK parses the body. A request that carries a token is
 * always verified, even for a public tool. It throws a `TypeError` for a
 * config with no token type or an invalid issuer config.
 *
 * @example
 * ```ts
 * const auth = createMcpAuthMiddleware({
 *   agentJwt: { audience: "https://mcp.example.com/mcp", resolveAgent },
 *   publicTools: ["echo"],
 * });
 * const app = auth.protect(createMcpHandler(buildServer, { legacy: "reject" }));
 * createServer(toNodeHandler(app)).listen(8787);
 * ```
 */
export function createMcpAuthMiddleware(config: McpAuthConfig): McpAuthMiddleware {
  const verifierFor = tokenVerifierRouter(
    config.agentJwt && createAgentJwtVerifier(config.agentJwt),
    config.externalTokens && createExternalTokenVerifier(config.externalTokens),
  );
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
      const authInfo = await verifierFor(token).verifyAccessToken(token);
      return resourceMetadataUrl === undefined ? authInfo : { ...authInfo, resourceMetadataUrl };
    } catch (error) {
      // The verifier throws OAuthError only with fixed text; anything else is a 500 with no detail.
      if (!(error instanceof OAuthError)) {
        reportInternalError(error);
      }
      return bearerAuthChallengeResponse(error, { resourceMetadataUrl });
    }
  }

  function protect(handler: McpFetchHandler): McpProtectedHandler {
    return {
      fetch: async (request: Request): Promise<Response> => {
        const authInfo = await authenticate(request);
        if (authInfo instanceof Response) {
          return authInfo;
        }
        return handler.fetch(request, authInfo === undefined ? undefined : { authInfo });
      },
    };
  }

  const { protectTool, scopeChallenge } = createGrantLayer({
    ...config,
    publicTools,
    externalIssuers: config.externalTokens?.issuers,
    reportInternalError,
  });
  return { authenticate, protect, protectTool, scopeChallenge };
}
