import { MCP_PROTOCOL_VERSION } from "../src/index.js";

/** Requests for the token-layer tests. The token layer reads headers only, never the body. */
export const MCP_ENDPOINT = "https://mcp.example.com/mcp";
export const PUBLIC_TOOL = "echo";
export const PROTECTED_TOOL = "secure_action";

export type HeaderList = [string, string][];

export function post(headers: HeaderList, body: unknown = {}): Request {
  return new Request(MCP_ENDPOINT, {
    method: "POST",
    headers: [["content-type", "application/json"], ...headers],
    body: JSON.stringify(body),
  });
}

export function toolsCallHeaders(tool: string): HeaderList {
  return [["mcp-protocol-version", MCP_PROTOCOL_VERSION], ["mcp-method", "tools/call"], ["mcp-name", tool]];
}

/** No-token requests that the header rule lets through. */
export const PUBLIC_REQUESTS: [string, HeaderList][] = [
  ["tools/call for a public tool", toolsCallHeaders(PUBLIC_TOOL)],
  ["tools/list", [["mcp-protocol-version", MCP_PROTOCOL_VERSION], ["mcp-method", "tools/list"]]],
  ["server/discover", [["mcp-protocol-version", MCP_PROTOCOL_VERSION], ["mcp-method", "server/discover"]]],
];

/**
 * No-token requests that must not pass as public. The first ones are the
 * forged variants (see docs/adapters/mcp-auth-bridge.md): a 2025-era request skips the
 * SDK's check of Mcp-Name against the body.
 */
export const NOT_PUBLIC_REQUESTS: [string, HeaderList, unknown?][] = [
  [
    "a 2025-era body with a forged Mcp-Name and no version header",
    [["mcp-method", "tools/call"], ["mcp-name", PUBLIC_TOOL]],
    { jsonrpc: "2.0", id: 1, method: "tools/call", params: { name: PROTECTED_TOOL, arguments: {} } },
  ],
  ["a public tool with no version header", toolsCallHeaders(PUBLIC_TOOL).slice(1)],
  ["a public tool with the 2025-11-25 version", [["mcp-protocol-version", "2025-11-25"], ...toolsCallHeaders(PUBLIC_TOOL).slice(1)]],
  ["two version headers", [["mcp-protocol-version", "2025-11-25"], ...toolsCallHeaders(PUBLIC_TOOL)]],
  ["two Mcp-Name values", [...toolsCallHeaders(PUBLIC_TOOL), ["mcp-name", PROTECTED_TOOL]]],
  ["a protected tool", toolsCallHeaders(PROTECTED_TOOL)],
  ["no Mcp-Method", [["mcp-protocol-version", MCP_PROTOCOL_VERSION], ["mcp-name", PUBLIC_TOOL]]],
  ["tools/call with no Mcp-Name", toolsCallHeaders(PUBLIC_TOOL).slice(0, 2)],
  ["initialize", [["mcp-protocol-version", MCP_PROTOCOL_VERSION], ["mcp-method", "initialize"]]],
];
