import { describe, expect, it } from "vitest";

import { isAllowedMcpRequest, isPublicMcpRequest, MCP_PROTOCOL_VERSION } from "../src/public-request.js";
import { NOT_PUBLIC_REQUESTS, post, PUBLIC_REQUESTS, PUBLIC_TOOL, toolsCallHeaders } from "./mcp-requests.js";

const publicTools: ReadonlySet<string> = new Set([PUBLIC_TOOL]);

describe("isPublicMcpRequest (header rule for requests with no token)", () => {
  it.each(PUBLIC_REQUESTS)("passes %s", (_case, headers) => {
    expect(isPublicMcpRequest(post(headers).headers, publicTools)).toBe(true);
  });

  it.each(NOT_PUBLIC_REQUESTS)("does not pass %s", (_case, headers, body) => {
    expect(isPublicMcpRequest(post(headers, body).headers, publicTools)).toBe(false);
  });

  it("passes no tools/call when the server has no public tool", () => {
    expect(isPublicMcpRequest(post(toolsCallHeaders(PUBLIC_TOOL)).headers, new Set())).toBe(false);
  });

  it("matches tool names case-sensitively", () => {
    expect(isPublicMcpRequest(post(toolsCallHeaders("Echo")).headers, publicTools)).toBe(false);
  });
});

describe("isAllowedMcpRequest (the same rule for a request with a verified token)", () => {
  const headersFor = (method: string): Headers => post([["mcp-protocol-version", MCP_PROTOCOL_VERSION], ["mcp-method", method]]).headers;

  it("passes an extra method only when it is listed", () => {
    expect(isAllowedMcpRequest(headersFor("resources/read"), publicTools, new Set(["resources/read"]))).toBe(true);
    expect(isAllowedMcpRequest(headersFor("prompts/get"), publicTools, new Set(["resources/read"]))).toBe(false);
  });

  it("does not pass tools/call through an extra method: only a callable tool passes", () => {
    expect(isAllowedMcpRequest(post(toolsCallHeaders("other")).headers, publicTools, new Set(["tools/call"]))).toBe(false);
  });

  it("does not pass the base64 form of a callable tool name", () => {
    const encoded = `=?base64?${Buffer.from(PUBLIC_TOOL).toString("base64")}?=`;

    expect(isAllowedMcpRequest(post(toolsCallHeaders(encoded)).headers, publicTools, new Set())).toBe(false);
  });
});
