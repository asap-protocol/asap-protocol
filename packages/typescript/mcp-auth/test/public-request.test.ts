import { describe, expect, it } from "vitest";

import { isPublicMcpRequest } from "../src/public-request.js";
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
