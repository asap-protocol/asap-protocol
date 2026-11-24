/**
 * RFC 9728 protected-resource metadata for the MCP endpoint. MCP authorization
 * says a protected MCP server MUST serve it. It names only the issuers whose
 * tokens the server really accepts; it serves no authorization-server
 * metadata and invents no authorization or token endpoint.
 */
import type { ExternalIssuerConfig } from "@asap-protocol/mcp-auth";
import { getOAuthProtectedResourceMetadataUrl, type OAuthProtectedResourceMetadata } from "@modelcontextprotocol/server";

const ALLOWED_METHODS = "GET, HEAD";

export interface ProtectedResourceMetadata {
  /** The well-known URL, for the middleware's `resourceMetadataUrl` (the 401 challenge). */
  readonly url: string;
  /** The document's `Response` for a request to its path, or `undefined` for any other path. */
  respond(request: Request): Response | undefined;
}

/**
 * The document for `resource`, or `undefined` when no external issuer is
 * configured: with Agent JWTs alone the server uses a custom auth strategy, not
 * MCP authorization. The SDK's `oauthMetadataResponse` is not used, because it
 * needs full authorization-server metadata and lists one issuer only.
 */
export function protectedResourceMetadata(resource: URL, issuers: readonly ExternalIssuerConfig[]): ProtectedResourceMetadata | undefined {
  if (issuers.length === 0) return undefined;
  const url = getOAuthProtectedResourceMetadataUrl(resource);
  const path = new URL(url).pathname;
  const scopes = issuers.flatMap((issuer) => (issuer.grantRules ?? []).flatMap((rule) => (rule.scope === undefined ? [] : [rule.scope])));
  const document: OAuthProtectedResourceMetadata = {
    resource: resource.href,
    authorization_servers: issuers.map((issuer) => issuer.issuer),
    scopes_supported: [...new Set(scopes)],
    bearer_methods_supported: ["header"],
  };
  return {
    url,
    respond: (request) => {
      if (new URL(request.url).pathname !== path) return undefined;
      if (request.method !== "GET" && request.method !== "HEAD") return new Response(null, { status: 405, headers: { Allow: ALLOWED_METHODS } });
      const response = Response.json(document);
      return request.method === "HEAD" ? new Response(null, { headers: response.headers }) : response;
    },
  };
}
