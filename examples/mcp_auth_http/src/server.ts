/**
 * Streamable HTTP MCP server on `@modelcontextprotocol/server` 2.x, protected
 * by `@asap-protocol/mcp-auth`: one public tool and one protected tool.
 * Run: `pnpm --filter example-mcp-auth-http start` (PORT, default 8787).
 */
import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import { pathToFileURL } from "node:url";

import { createMcpAuthMiddleware, type McpAuthMiddleware } from "@asap-protocol/mcp-auth";
import { toNodeHandler } from "@modelcontextprotocol/node";
import {
  createMcpHandler,
  hostHeaderValidationResponse,
  localhostAllowedHostnames,
  localhostAllowedOrigins,
  McpServer,
  originValidationResponse,
} from "@modelcontextprotocol/server";
import * as z from "zod";

import { createDemoIdentity, type DemoIdentity } from "./demo-identity.js";

export const PUBLIC_TOOL = "echo";
export const PROTECTED_TOOL = "secure_action";

/** A fresh server per request: the 2026-07-28 revision has no session. */
export function buildExampleMcpServer(auth: McpAuthMiddleware): McpServer {
  const server = new McpServer({ name: "mcp-auth-http-example", version: "0.1.0" });
  server.registerTool(
    PUBLIC_TOOL,
    { description: "Public echo tool (no token needed)", inputSchema: z.object({ message: z.string() }) },
    async ({ message }) => ({ content: [{ type: "text", text: message }] }),
  );
  server.registerTool(
    PROTECTED_TOOL,
    {
      description: "Protected action (Agent JWT or OAuth 2.1 access token, and a grant)",
      inputSchema: z.object({ action: z.string() }),
      scopeChallenge: auth.scopeChallenge,
    },
    auth.protectTool(PROTECTED_TOOL, async ({ action }) => ({ content: [{ type: "text", text: `executed: ${action}` }] })),
  );
  return server;
}

export interface RunningExampleServer {
  url: URL;
  demo: DemoIdentity;
  close(): Promise<void>;
}

export async function startExampleServer(port = 0, host = "127.0.0.1"): Promise<RunningExampleServer> {
  const demo = await createDemoIdentity();
  // The audience is the endpoint URL, which is known only once the port is bound.
  let app: { fetch(request: Request): Promise<Response> } | undefined;
  const httpServer = createServer(toNodeHandler({ fetch: (request: Request) => (app as NonNullable<typeof app>).fetch(request) }));
  await new Promise<void>((resolve) => httpServer.listen(port, host, resolve));
  const url = new URL(`http://${host}:${(httpServer.address() as AddressInfo).port}/mcp`);

  const auth = createMcpAuthMiddleware({
    agentJwt: { audience: url.href, resolveAgent: demo.resolveAgent },
    agentGrants: demo.agentGrants,
    publicTools: [PUBLIC_TOOL],
  });
  // legacy: "reject" serves only 2026-07-28. A 2025-era request skips the SDK's check of
  // Mcp-Method and Mcp-Name against the body, which the token layer relies on.
  const mcpHandler = createMcpHandler(() => buildExampleMcpServer(auth), { legacy: "reject" });
  const protectedMcp = auth.protect(mcpHandler);
  app = {
    fetch: async (request) =>
      hostHeaderValidationResponse(request, localhostAllowedHostnames()) ??
      originValidationResponse(request, localhostAllowedOrigins()) ??
      protectedMcp.fetch(request),
  };
  return {
    url,
    demo,
    close: async () => {
      await mcpHandler.close();
      await new Promise<void>((resolve, reject) => httpServer.close((error) => (error ? reject(error) : resolve())));
    },
  };
}

if (process.argv[1] !== undefined && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const running = await startExampleServer(Number(process.env.PORT ?? 8787));
  console.log(`MCP endpoint: ${running.url.href}`);
}
