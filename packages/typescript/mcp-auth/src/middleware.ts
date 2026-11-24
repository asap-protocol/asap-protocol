import { AGENT_JWT_TYP, JtiReplayCacheFullError } from "@asap-protocol/client";
import {
  bearerAuthChallengeResponse,
  isLegacyRequest,
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
import { isAllowedMcpRequest, isPublicMcpRequest } from "./public-request.js";
import { JwksUnavailableError } from "./remote-jwks.js";

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
  /** Tools that a request with no token may call, under the header rule. A request with a token may call them too. */
  readonly publicTools?: readonly string[];
  /**
   * The protected tools: tool name → ASAP capability. Deny by default: a
   * request with a token may call only the tools listed here and in
   * `publicTools`; any other `tools/call` gets HTTP 403. Wrap each listed tool
   * with `protectTool`, which checks the grant.
   */
  readonly toolCapabilities?: Readonly<Record<string, string>>;
  /**
   * Methods, besides `tools/call`, `tools/list`, and `server/discover`, that a
   * request with a verified token may call, such as `"resources/read"` or
   * `"prompts/get"`. Default: none, so every other method gets HTTP 403
   * before any handler runs. A listed method runs with no grant check. It
   * cannot list `"tools/call"`, and it opens nothing to a request with no token.
   */
  readonly allowMethods?: readonly string[];
  /** The grants of a verified Agent JWT principal, for example `staticAgentGrants(...)`. Default: none (deny). */
  readonly agentGrants?: AgentGrantResolver;
  /** RFC 9728 metadata URL: `resource_metadata` in every 401 challenge, and in `AuthInfo` for the SDK's 403 challenge. */
  readonly resourceMetadataUrl?: string;
  /**
   * Called when verification or a grant lookup fails for a reason other than
   * the token, such as a resolver that throws (HTTP 500 with no detail), an
   * issuer's JWKS that cannot be fetched (HTTP 503), or a `protectTool`
   * callback registered under another tool's name. The package itself never
   * logs, and this hook never receives the token.
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
   * request with no token, or the `Response` to return (401; 403 for a
   * verified token on a tool or method that the config does not declare; 503
   * when an issuer's keys cannot be fetched or the replay cache is full; 500
   * with no detail). It never throws.
   */
  authenticate(request: Request): Promise<AuthInfo | Response | undefined>;
  /**
   * Put the token layer in front of an MCP handler. SDK 2.4 has no request
   * middleware, so the layer wraps `handler.fetch`: a refused request gets its
   * `Response`, and any other request reaches `handler.fetch(request, { authInfo })`.
   * Tools read the result as `ctx.http?.authInfo`. A request that the SDK's
   * `isLegacyRequest` would route to 2025-era serving is refused too (401 with
   * no token, 403 with one), because that path skips the SDK's `Mcp-Name` check.
   */
  protect(handler: McpFetchHandler): McpProtectedHandler;
  /**
   * The grant layer: wrap a tool callback, as the third argument of
   * `registerTool` under the same name. The name must be in
   * `toolCapabilities` and not in `publicTools`, or it throws a `TypeError`.
   * A call runs only when its `Mcp-Name` is that name, the principal in
   * `ctx.http.authInfo` holds a grant for the tool's capability, and the call
   * arguments meet its constraints; otherwise the result is a tool error
   * (`asap:capability_denied` or `asap:constraint_violation`). A protected
   * tool with no verified principal gets `asap:auth_required`.
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
const SERVICE_UNAVAILABLE_TEXT = "Authentication service unavailable";
// The SDK decodes this form of Mcp-Name before it compares it with the body.
const BASE64_SENTINEL_PREFIX = "=?base64?";

/** No token: per RFC 6750 §3.1 the challenge has no `error` parameter; the body still names the ASAP code. */
function authRequiredResponse(resourceMetadataUrl: string | undefined): Response {
  const body = new OAuthError(OAuthErrorCode.InvalidToken, McpAuthErrorCode.AuthRequired).toResponseObject();
  const challenge = resourceMetadataUrl === undefined ? "Bearer" : `Bearer resource_metadata="${resourceMetadataUrl}"`;
  return Response.json(body, { status: 401, headers: { "WWW-Authenticate": challenge } });
}

/**
 * The issuer's keys cannot be fetched (Python parity: 503 "Authentication
 * service unavailable"), or the replay cache is full of live records.
 * Fixed text, no host and no cause; `Retry-After` is the time until
 * the key source may fetch again, or until enough cache records expire.
 */
function serviceUnavailableResponse(error: JwksUnavailableError | JtiReplayCacheFullError): Response {
  const body = new OAuthError(OAuthErrorCode.TemporarilyUnavailable, SERVICE_UNAVAILABLE_TEXT).toResponseObject();
  return Response.json(body, { status: 503, headers: { "Retry-After": String(error.retryAfterSeconds) } });
}

/**
 * A verified token for a tool or method that the config does not declare
 * (deny by default). The same shape as the external-token scope challenge,
 * with no `scope` parameter, because no scope grants an undeclared method.
 */
function capabilityDeniedResponse(resourceMetadataUrl: string | undefined): Response {
  const error = new OAuthError(OAuthErrorCode.InsufficientScope, McpAuthErrorCode.CapabilityDenied);
  return bearerAuthChallengeResponse(error, { resourceMetadataUrl });
}

/** What a verified token may reach (deny by default). A config that would weaken the header rule is a `TypeError`. */
function tokenSurface(config: McpAuthConfig): { callableTools: ReadonlySet<string>; allowMethods: ReadonlySet<string> } {
  const callableTools = new Set([...(config.publicTools ?? []), ...Object.keys(config.toolCapabilities ?? {})]);
  // The header rule compares the raw Mcp-Name; a name in the encoded form would match a different decoded tool.
  const encoded = [...callableTools].find((tool) => tool.startsWith(BASE64_SENTINEL_PREFIX));
  if (encoded !== undefined) throw new TypeError(`tool name ${JSON.stringify(encoded)} has the base64 form of an Mcp-Name value; choose another name`);
  const allowMethods = new Set(config.allowMethods);
  if (allowMethods.has("tools/call")) throw new TypeError('allowMethods cannot list "tools/call": publicTools and toolCapabilities name the tools a token may call');
  return { callableTools, allowMethods };
}

/**
 * The header rule holds only on the SDK's 2026-07-28 path.
 * `isLegacyRequest` runs the SDK's own routing step on a clone of the body
 * (with its default 4 MiB limit). Under SDK 2.4.0 every request that passes
 * the header rule routes there already, so this guards against a later SDK
 * 2.x that routes differently. A body that cannot be read counts as legacy.
 */
async function routedAsLegacy(request: Request): Promise<boolean> {
  try {
    return await isLegacyRequest(request);
  } catch {
    return true;
  }
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
 * always verified, even for a public tool, and then reaches only the tools and
 * methods that the config declares (deny by default). It throws a `TypeError`
 * for a config with no token type, an invalid issuer config, or `tools/call`
 * in `allowMethods`.
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
  const { callableTools, allowMethods } = tokenSurface(config);
  // URL#href percent-encodes quotes, backslashes, and spaces, so it is safe inside a quoted-string.
  const resourceMetadataUrl = config.resourceMetadataUrl === undefined ? undefined : new URL(config.resourceMetadataUrl).href;

  function reportInternalError(error: unknown): void {
    try {
      config.onInternalError?.(error);
    } catch {
      // A failing hook must not turn the 500 or 503 into an unhandled throw.
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
    let authInfo: AuthInfo;
    try {
      authInfo = await verifierFor(token).verifyAccessToken(token);
    } catch (error) {
      // The verifier throws OAuthError only with fixed text; a key source that cannot be
      // reached or a full replay cache is a 503; anything else is a 500 with no detail.
      if (!(error instanceof OAuthError)) {
        reportInternalError(error);
      }
      if (error instanceof JwksUnavailableError || error instanceof JtiReplayCacheFullError) {
        return serviceUnavailableResponse(error);
      }
      return bearerAuthChallengeResponse(error, { resourceMetadataUrl });
    }
    if (!isAllowedMcpRequest(request.headers, callableTools, allowMethods)) {
      return capabilityDeniedResponse(resourceMetadataUrl);
    }
    return resourceMetadataUrl === undefined ? authInfo : { ...authInfo, resourceMetadataUrl };
  }

  function protect(handler: McpFetchHandler): McpProtectedHandler {
    return {
      fetch: async (request: Request): Promise<Response> => {
        const authInfo = await authenticate(request);
        if (authInfo instanceof Response) {
          return authInfo;
        }
        if (await routedAsLegacy(request)) {
          return authInfo === undefined ? authRequiredResponse(resourceMetadataUrl) : capabilityDeniedResponse(resourceMetadataUrl);
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
