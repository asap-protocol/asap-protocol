import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { assertValidGrantRules, mapExternalGrants, type CapabilityGrant, type ExternalGrantRule } from "../src/index.js";

interface GrantMappingVector {
  name: string;
  claims: Record<string, unknown>;
  rules: ExternalGrantRule[];
  expected_grants: CapabilityGrant[];
}

// Shared with tests/auth/test_external_grants.py (Python asap.auth.external_grants).
const VECTOR_URL = new URL("../../../../tests/fixtures/interop/grant-mapping.json", import.meta.url);
const vectors = (JSON.parse(readFileSync(VECTOR_URL, "utf8")) as { vectors: GrantMappingVector[] }).vectors;

describe("mapExternalGrants (shared vectors, grant-mapping.json)", () => {
  it.each(vectors)("$name", ({ claims, rules, expected_grants }) => {
    assertValidGrantRules(rules);

    expect(mapExternalGrants(claims, rules)).toEqual(expected_grants);
  });

  it("never reads an inherited claim", () => {
    expect(mapExternalGrants({}, [{ claim: "constructor", value: "Object", capability: "a.cap" }])).toEqual([]);
  });
});

describe("assertValidGrantRules: one well-formed matcher per rule (Python ExternalGrantRule)", () => {
  it.each<[string, unknown]>([
    ["no matcher", { capability: "file.read" }],
    ["a scope and a claim", { scope: "files:read", claim: "groups", value: "x", capability: "file.read" }],
    ["a claim with no value", { claim: "groups", capability: "file.read" }],
    ["a scope with two tokens", { scope: "files:read files:write", capability: "file.read" }],
    ["an empty scope", { scope: "", capability: "file.read" }],
    ["a claim rule on the scope claim", { claim: "scope", value: "files:read", capability: "file.read" }],
    ["an empty capability", { scope: "files:read", capability: "" }],
    ["a scope outside the RFC 6749 scope-token grammar", { scope: 'files"read', capability: "file.read" }],
    ["an unknown key", { scope: "files:read", capability: "file.read", constraint: { path: "/a" } }],
  ])("refuses %s", (_case, rule) => {
    expect(() => assertValidGrantRules([rule as ExternalGrantRule])).toThrow(/^grant rule/);
  });

  it.each<[string, Record<string, unknown>]>([
    ["a string max", { amount: { max: "100" } }],
    ["a boolean max", { amount: { max: true } }],
    ["an infinite min", { amount: { min: Number.POSITIVE_INFINITY } }],
    ["in on a string", { env: { in: "dev,staging" } }],
    ["in on an object", { tier: { in: { gold: 1 } } }],
    ["an object with no operator key", { opts: { eq: 5 } }],
    ["an unknown key beside an operator", { amount: { max: 5, mxa: 3 } }],
    ["an empty operator object", { opts: {} }],
    ["constraints that are not an object", ["path"] as unknown as Record<string, unknown>],
  ])("refuses constraints with %s, which validateConstraints would misread", (_case, constraints) => {
    expect(() => assertValidGrantRules([{ scope: "files:read", capability: "file.read", constraints }])).toThrow(/constraint/);
  });

  it("accepts well-formed constraints", () => {
    const constraints = { amount: { max: 5, min: 0.5 }, env: { in: ["dev"], not_in: [] }, region: "eu" };

    expect(() => assertValidGrantRules([{ scope: "files:read", capability: "file.read", constraints }])).not.toThrow();
  });
});

describe("assertValidGrantRules: one constraint set per capability", () => {
  it("refuses rules that give one capability two constraint sets, in either order", () => {
    const rules: ExternalGrantRule[] = [
      { scope: "files:read", capability: "file.read", constraints: { path: "/a" } },
      { scope: "files:all", capability: "file.read" },
    ];

    expect(() => assertValidGrantRules(rules)).toThrow(/file\.read/);
    expect(() => assertValidGrantRules([...rules].reverse())).toThrow(/file\.read/);
  });

  it("accepts rules that give one capability equal constraints", () => {
    const rules: ExternalGrantRule[] = [
      { scope: "files:read", capability: "file.read", constraints: { path: "/a", n: 1 } },
      { claim: "groups", value: "readers", capability: "file.read", constraints: { n: 1.0, path: "/a" } },
    ];

    expect(() => assertValidGrantRules(rules)).not.toThrow();
  });
});
