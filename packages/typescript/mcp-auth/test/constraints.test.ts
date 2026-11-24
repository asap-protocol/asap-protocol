import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { validateConstraints, type ConstraintSet } from "../src/index.js";

interface FieldOperator {
  field: string;
  operator: string;
}

interface ConstraintVector {
  name: string;
  constraints: ConstraintSet;
  arguments: Record<string, unknown>;
  violations: FieldOperator[];
}

// Shared with tests/auth/test_constraint_vectors.py (Python asap.auth.capabilities.validate_constraints).
const VECTOR_URL = new URL("../../../../tests/fixtures/interop/constraints.json", import.meta.url);
const vectors = (JSON.parse(readFileSync(VECTOR_URL, "utf8")) as { vectors: ConstraintVector[] }).vectors;

/** The vector format: field and operator only, as a sorted list; message text is never compared. */
function fieldOperators(constraints: ConstraintSet, args: Record<string, unknown>): FieldOperator[] {
  return validateConstraints(constraints, args)
    .map(({ field, operator }) => ({ field, operator }))
    .sort((a, b) => `${a.field}\u0000${a.operator}`.localeCompare(`${b.field}\u0000${b.operator}`));
}

describe("validateConstraints (shared vectors, constraints.json)", () => {
  it.each(vectors)("$name", ({ constraints, arguments: args, violations }) => {
    expect(fieldOperators(constraints, args)).toEqual(violations);
  });
});

// Fail closed: what cannot be evaluated is a violation of that field and operator, never a pass and
// never a throw. The shared vectors do not carry these cases yet (JSON cannot hold NaN or Infinity).
describe("validateConstraints: cases that cannot be evaluated fail closed", () => {
  it.each<[string, ConstraintSet, Record<string, unknown>, FieldOperator[]]>([
    ["a NaN argument to max and min", { n: { max: 10, min: 0 } }, { n: Number.NaN }, [{ field: "n", operator: "max" }, { field: "n", operator: "min" }]],
    ["an Infinity argument to min", { n: { min: 0 } }, { n: Number.POSITIVE_INFINITY }, [{ field: "n", operator: "min" }]],
    ["a -Infinity argument to max", { n: { max: 10 } }, { n: Number.NEGATIVE_INFINITY }, [{ field: "n", operator: "max" }]],
    ["a string bound", { n: { max: "10" } }, { n: 5 }, [{ field: "n", operator: "max" }]],
    ["a boolean bound", { n: { min: false } }, { n: 5 }, [{ field: "n", operator: "min" }]],
    ["a null bound", { n: { max: null } }, { n: 5 }, [{ field: "n", operator: "max" }]],
    ["a non-finite bound", { n: { max: Number.POSITIVE_INFINITY } }, { n: 5 }, [{ field: "n", operator: "max" }]],
    ["a list argument to max", { n: { max: 10 } }, { n: [1] }, [{ field: "n", operator: "max" }]],
    ["in on a string with a number argument", { path: { in: "/srv/1" } }, { path: 1 }, [{ field: "path", operator: "in" }]],
    ["not_in on a string with a number argument", { path: { not_in: "/etc" } }, { path: 1 }, [{ field: "path", operator: "not_in" }]],
    ["in on an object with a list argument", { tier: { in: { gold: 1 } } }, { tier: ["gold"] }, [{ field: "tier", operator: "in" }]],
    ["not_in on an object with a number argument", { tier: { not_in: { gold: 1 } } }, { tier: 1 }, [{ field: "tier", operator: "not_in" }]],
    ["in with a null container", { env: { in: null } }, { env: "dev" }, [{ field: "env", operator: "in" }]],
    ["not_in with a number container", { env: { not_in: 5 } }, { env: 5 }, [{ field: "env", operator: "not_in" }]],
  ])("%s", (_case, constraints, args, expected) => {
    expect(fieldOperators(constraints, args)).toEqual(expected);
  });

  it("never reads an inherited property as an argument or an operator", () => {
    expect(fieldOperators({ toString: "x" }, {})).toEqual([{ field: "toString", operator: "required" }]);
    expect(fieldOperators({ opts: Object.create({ max: 1 }) as ConstraintSet }, { opts: 5 })).toEqual([{ field: "opts", operator: "eq" }]);
  });

  it("counts an undefined argument as missing", () => {
    expect(fieldOperators({ env: "dev" }, { env: undefined })).toEqual([{ field: "env", operator: "required" }]);
  });
});

describe("validateConstraints: Python equality on nested values", () => {
  it.each<[string, unknown, unknown, boolean]>([
    ["false == 0", 0, false, true],
    ["equal lists with true == 1", ["a", 1], ["a", true], true],
    ["lists of different length", [1, 2], [1], false],
    ["a list and an object", [1], { 0: 1 }, false],
    ["equal objects with 1 == 1.0", { b: { c: 1 } }, { b: { c: 1.0 } }, true],
    ["objects with different keys", { a: 1 }, { b: 1 }, false],
    ["objects with an extra key", { a: 1 }, { a: 1, b: 2 }, false],
    ["a string and a number", "1", 1, false],
  ])("%s", (_case, granted, actual, equal) => {
    expect(validateConstraints({ v: granted }, { v: actual })).toHaveLength(equal ? 0 : 1);
    expect(validateConstraints({ v: { in: [granted] } }, { v: actual })).toHaveLength(equal ? 0 : 1);
  });
});

describe("validateConstraints: messages", () => {
  it("names the field and the bound, as Python does", () => {
    const [violation] = validateConstraints({ tokens: { max: 100 } }, { tokens: 150 });

    expect(violation).toEqual({ field: "tokens", operator: "max", message: "tokens: 150 exceeds maximum 100" });
  });
});
