/**
 * Streamable HTTP MCP server on `@modelcontextprotocol/server` 2.x, for
 * `@asap-protocol/mcp-auth`. One public tool and one tool to protect.
 * Run: `pnpm --filter example-mcp-auth-http start` (PORT, default 8787).
 */
import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import { pathToFileURL } from "node:url";

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

export const PUBLIC_TOOL = "echo";
export const PROTECTED_TOOL = "secure_action";

/** A fresh server per request: the 2026-07-28 revision has no session. */
export function buildExampleMcpServer(): McpServer {
  const server = new McpServer({ name: "mcp-auth-http-example", version: "0.1.0" });
  server.registerTool(
    PUBLIC_TOOL,
    { description: "Public echo tool (no token needed)", inputSchema: z.object({ message: z.string() }) },
    async ({ message }) => ({ content: [{ type: "text", text: message }] }),
  );
  server.registerTool(
    PROTECTED_TOOL,
    { description: "Protected action (Agent JWT or OAuth 2.1 token)", inputSchema: z.object({ action: z.string() }) },
    async ({ action }) => ({ content: [{ type: "text", text: `executed: ${action}` }] }),
  );
  return server;
}

export interface RunningExampleServer {
  url: URL;
  close(): Promise<void>;
}

export async function startExampleServer(port = 0, host = "127.0.0.1"): Promise<RunningExampleServer> {
  // legacy: "reject" serves only 2026-07-28. A 2025-era request skips the SDK's check of
  // Mcp-Method and Mcp-Name against the body, which the token layer relies on.
  const mcpHandler = createMcpHandler(buildExampleMcpServer, { legacy: "reject" });
  const localOnly = {
    fetch: async (request: Request): Promise<Response> =>
      hostHeaderValidationResponse(request, localhostAllowedHostnames()) ??
      originValidationResponse(request, localhostAllowedOrigins()) ??
      mcpHandler.fetch(request),
  };
  const httpServer = createServer(toNodeHandler(localOnly));
  await new Promise<void>((resolve) => httpServer.listen(port, host, resolve));
  const { port: boundPort } = httpServer.address() as AddressInfo;
  return {
    url: new URL(`http://${host}:${boundPort}/mcp`),
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
