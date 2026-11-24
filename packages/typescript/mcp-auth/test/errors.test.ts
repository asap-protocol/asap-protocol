import { readFileSync } from "node:fs";

import { isCallToolResult } from "@modelcontextprotocol/server";
import { describe, expect, it } from "vitest";

import { McpAuthErrorCode, toolErrorResult } from "../src/index.js";

interface ToolErrorVectorCase {
  code: string;
  detail: string | null;
  result: unknown;
}

interface McpAuthErrorsVector {
  codes: string[];
  tool_error_results: ToolErrorVectorCase[];
}

// Shared with tests/adapters/mcp/test_errors_vector.py (Python asap.mcp.auth.errors).
const VECTOR_URL = new URL("../../../../tests/fixtures/interop/mcp-auth-errors.json", import.meta.url);
const vector = JSON.parse(readFileSync(VECTOR_URL, "utf8")) as McpAuthErrorsVector;

const knownCodes: readonly string[] = Object.values(McpAuthErrorCode);

function asErrorCode(value: string): McpAuthErrorCode {
  const code = Object.values(McpAuthErrorCode).find((known) => known === value);
  if (code === undefined) {
    throw new Error(`vector code ${JSON.stringify(value)} is not one of ${knownCodes.join(", ")}`);
  }
  return code;
}

describe("MCP auth error codes (shared vector)", () => {
  it("defines exactly the four codes of the Python bridge", () => {
    expect([...knownCodes].sort()).toEqual([...vector.codes].sort());
  });

  it.each(vector.tool_error_results)("builds the tool-error payload for $code", ({ code, detail, result }) => {
    expect(toolErrorResult(asErrorCode(code), detail ?? undefined)).toEqual(result);
  });

  it("returns a payload that the MCP SDK accepts as a CallToolResult", () => {
    expect(isCallToolResult(toolErrorResult(McpAuthErrorCode.CapabilityDenied, "no grant"))).toBe(true);
  });
});
