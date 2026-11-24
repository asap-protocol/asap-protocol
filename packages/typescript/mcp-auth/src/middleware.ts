import { AGENT_JWT_TYP, JtiReplayCacheFullError } from "@asap-protocol/client";
import {
  bearerAuthChallengeResponse,
  DEFAULT_MAX_REQUEST_BODY_SIZE,
  isJsonContentType,
  isJSONRPCNotification,
  isLegacyRequest,
  OAuthError,
  OAuthErrorCode,
  readRequestBody,
  type AuthInfo,
  type McpHandlerRequestOptions,
  type OAuthTokenVerifier,
  type ScopeChallengeHandler,
} from "@modelcontextprotocol/server";
import { decodeProtectedHeader } from "jose";

import { createAgentJwtVerifier, type AgentJwtVerifierConfig } from "./agent-jwt-verifier.js";
import { isJsonObject } from "./constraints.js";
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
   * A JSON-RPC notification passes only when its method is listed here.
   */
  readonly allowMethods?: readonly string[];
  /** The grants of a verified Agent JWT principal, for example `staticAgentGrants(...)`. Default: none (deny). */
  readonly agentGrants?: AgentGrantResolver;
  /**
   * RFC 9728 metadata URL: `resource_metadata` in every 401 and 403
   * challenge, and in `AuthInfo` for the SDK's 403 challenge. Required with
   * `externalTokens`, because the server is then the resource server of MCP
   * authorization, which must serve this metadata.
   */
  readonly resourceMetadataUrl?: string;
  /**
   * Called when verification or a grant lookup fails for a reason other than
   * the token, such as a resolver that throws (HTTP 500 with no detail), an
   * issuer's JWKS that cannot be fetched (HTTP 503), a `protectTool`
   * callback registered under another tool's name, a protected tool
   * registered without `scopeChallenge` (seen when an access token lacks a
   * scope that a grant rule maps), or a tool in `toolCapabilities` whose call
   * got a result although `protectTool` never wrapped it. The package itself
   * never logs, and this hook never receives the token.
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
   * no token, 403 with one), because that path skips the SDK's `Mcp-Name` check;
   * so is a JSON-RPC notification whose method `allowMethods` does not list.
   * After the header checks, `protect` refuses a POST with an unsupported
   * media type (415) or a body over 4 MiB (413) before it calls the handler.
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
   * `asap:capability_denied`, before the tool runs. Without it, the call gets
   * a tool result, and `onInternalError` gets an error that names the tool.
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

interface BodyRoute {
  readonly legacy: boolean;
  /** The method of a JSON-RPC notification (a message with no `id`). */
  readonly notification?: string;
}

function bodyErrorResponse(status: number, message: string): Response {
  return Response.json({ jsonrpc: "2.0", id: null, error: { code: -32000, message } }, { status });
}

/**
 * The header rule holds on the SDK's 2026-07-28 path. Inspect the body to
 * confirm that route and detect notifications. Refuse excess data here because
 * the downstream handler can use a different limit. Treat a read or parse
 * error as legacy traffic.
 */
async function routeOfBody(request: Request): Promise<BodyRoute | Response> {
  if (request.method === "POST" && !isJsonContentType(request.headers.get("content-type"))) {
    return bodyErrorResponse(415, "Unsupported Media Type: Content-Type must be application/json");
  }
  try {
    const copy = request.clone();
    const read = await readRequestBody(copy);
    if (read.tooLarge) {
      // Release both branches of the body stream when neither handler will read them.
      void Promise.allSettled([copy.body?.cancel(), request.body?.cancel()]);
      return bodyErrorResponse(413, `Payload Too Large: Request body must not exceed ${DEFAULT_MAX_REQUEST_BODY_SIZE} bytes`);
    }
    const body: unknown = JSON.parse(read.text);
    return { legacy: await isLegacyRequest(request, body), notification: isJSONRPCNotification(body) ? body.method : undefined };
  } catch {
    return { legacy: true };
  }
}

/**
 * A tool in `toolCapabilities` that was registered without
 * `protectTool` runs with no grant check. The package does not see
 * `registerTool`, so it cannot stop that call; it can see, afterwards, that a
 * call to the tool got a result although `protectTool` never wrapped it. The
 * SDK registers tools before it dispatches, so a wrapped tool is known by then.
 * A JSON-RPC error (for example a tool that this request's server does not
 * register) and any answer other than 200 are not reported.
 */
async function resultOfUnwrappedTool(request: Request, response: Response, declared: ReadonlySet<string>, isWrapped: (name: string) => boolean): Promise<string | undefined> {
  const name = request.headers.get("mcp-name");
  const tool = [...declared].find((declaredTool) => declaredTool === name);
  if (request.headers.get("mcp-method") !== "tools/call" || tool === undefined || isWrapped(tool) || response.status !== 200) return undefined;
  if (String(response.headers.get("content-type")).startsWith("text/event-stream")) return tool;
  try {
    const body: unknown = await response.clone().json();
    return isJsonObject(body) && "result" in body ? tool : undefined;
  } catch {
    return undefined;
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
 * for a config with no token type, an invalid issuer config, `externalTokens`
 * with no `resourceMetadataUrl`, or `tools/call` in `allowMethods`.
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
  if (config.externalTokens !== undefined && config.resourceMetadataUrl === undefined) {
    throw new TypeError(
      "externalTokens needs resourceMetadataUrl: with external tokens the server is the resource server of MCP authorization, " +
        "which must serve RFC 9728 metadata; pass its URL, for example getOAuthProtectedResourceMetadataUrl(new URL(<MCP endpoint>))",
    );
  }
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
        if (route instanceof Response) return route;
        const authInfo = await authenticate(request);
        if (authInfo instanceof Response) {
          return authInfo;
        }
        const route = await routeOfBody(request);
        // A notification passes only when allowMethods lists it and the token verified: by default none reaches a handler.
        const deniedNotification = route.notification !== undefined && (authInfo === undefined || !allowMethods.has(route.notification));
        if (route.legacy || deniedNotification) {
          return authInfo === undefined ? authRequiredResponse(resourceMetadataUrl) : capabilityDeniedResponse(resourceMetadataUrl);
        }
        const response = await handler.fetch(request, authInfo === undefined ? undefined : { authInfo });
        const unwrapped = await resultOfUnwrappedTool(request, response, declaredProtected, grantLayer.isWrapped);
        if (unwrapped !== undefined) {
          const name = JSON.stringify(unwrapped);
          reportInternalError(new Error(`toolCapabilities lists ${name}, but protectTool(${name}, ...) was never called on this middleware: a call to the tool got a result with no grant check; register it as registerTool(${name}, config, auth.protectTool(${name}, callback))`));
        }
        return response;
      },
    };
  }

  const grantLayer = createGrantLayer({
    ...config,
    publicTools,
    externalIssuers: config.externalTokens?.issuers,
    reportInternalError,
  });
  const declaredProtected: ReadonlySet<string> = new Set(Object.keys(config.toolCapabilities ?? {}));
  return { authenticate, protect, protectTool: grantLayer.protectTool, scopeChallenge: grantLayer.scopeChallenge };
}
