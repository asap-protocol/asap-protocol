import type { ConstraintViolation } from "@asap-protocol/client";

/** Grant constraints: each field maps to a plain value (exact match) or to an object of operators. */
export type ConstraintSet = Readonly<Record<string, unknown>>;

/** Python `OPERATOR_KEYS` (`src/asap/auth/capabilities.py`). */
export const CONSTRAINT_OPERATORS: readonly string[] = ["max", "min", "in", "not_in"];

type JsonObject = Readonly<Record<string, unknown>>;

export function isJsonObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** A Python number: `bool` is a subclass of `int`, so `true` is 1. */
function asPythonNumber(value: unknown): number | undefined {
  if (typeof value === "boolean") return value ? 1 : 0;
  return typeof value === "number" ? value : undefined;
}

/**
 * Python `==` on JSON values: deep, with `true == 1` and `1 == 1.0`. The
 * recursion stops at the shallower side, and one side is always the
 * configured constraint, so a deep argument cannot exhaust the stack.
 */
export function pythonEquals(left: unknown, right: unknown): boolean {
  const leftNumber = asPythonNumber(left);
  const rightNumber = asPythonNumber(right);
  if (leftNumber !== undefined || rightNumber !== undefined) return leftNumber === rightNumber;
  if (Array.isArray(left) || Array.isArray(right)) {
    return Array.isArray(left) && Array.isArray(right) && left.length === right.length && left.every((item, index) => pythonEquals(item, right[index]));
  }
  if (isJsonObject(left) && isJsonObject(right)) {
    const keys = Object.keys(left);
    return keys.length === Object.keys(right).length && keys.every((key) => Object.hasOwn(right, key) && pythonEquals(left[key], right[key]));
  }
  return left === right;
}

/**
 * Python `item in container`, or `undefined` where Python raises or the type
 * mix has no meaning: a string or an object's keys can only hold a string.
 */
function pythonContains(container: unknown, item: unknown): boolean | undefined {
  if (Array.isArray(container)) return container.some((element) => pythonEquals(element, item));
  if (typeof item !== "string") return undefined;
  if (typeof container === "string") return container.includes(item);
  return isJsonObject(container) ? Object.hasOwn(container, item) : undefined;
}

function boundFailure(operator: string, bound: unknown, actual: unknown): string | undefined {
  const value = asPythonNumber(actual);
  if (typeof bound !== "number" || !Number.isFinite(bound) || value === undefined || !Number.isFinite(value)) {
    return `cannot check ${operator}: the bound and the argument must be finite numbers`;
  }
  if (operator === "max" && value > bound) return `${value} exceeds maximum ${bound}`;
  if (operator === "min" && value < bound) return `${value} is below minimum ${bound}`;
  return undefined;
}

function membershipFailure(operator: string, container: unknown, actual: unknown): string | undefined {
  const contained = pythonContains(container, actual);
  if (contained === undefined) return `cannot check ${operator}: the argument type does not fit the granted values`;
  if (operator === "in" && !contained) return "not in the allowed values";
  if (operator === "not_in" && contained) return "is in the forbidden values";
  return undefined;
}

function operatorFailure(operator: string, expected: unknown, actual: unknown): string | undefined {
  return operator === "max" || operator === "min"
    ? boundFailure(operator, expected, actual)
    : membershipFailure(operator, expected, actual);
}

/**
 * Port of Python `validate_constraints`: check call arguments against grant
 * constraints and return every violation (an empty list allows the call). A
 * missing or `null` argument fails `required`; a plain value, or an object
 * with no operator key, is an exact match (`eq`); `max`, `min`, `in`, and
 * `not_in` are each checked, and unknown keys next to them are ignored.
 * Shared vectors: `tests/fixtures/interop/constraints.json`.
 *
 * It fails closed where Python passes or raises: a non-finite number for
 * `max` or `min`, a bound that is not a finite number, and an `in` or
 * `not_in` whose argument type does not fit the container are violations.
 * It never throws, and it reads own properties only.
 *
 * @example
 * ```ts
 * validateConstraints({ amount: { max: 100 } }, { amount: 150 });
 * // [{ field: "amount", operator: "max", message: "amount: 150 exceeds maximum 100" }]
 * ```
 */
export function validateConstraints(constraints: ConstraintSet, args: Readonly<Record<string, unknown>>): ConstraintViolation[] {
  const violations: ConstraintViolation[] = [];
  const fail = (field: string, operator: string, detail: string) => violations.push({ field, operator, message: `${field}: ${detail}` });
  for (const [field, spec] of Object.entries(constraints)) {
    const actual = Object.hasOwn(args, field) ? args[field] : undefined;
    if (actual === undefined || actual === null) {
      fail(field, "required", "missing required argument");
      continue;
    }
    const operators = isJsonObject(spec) ? Object.keys(spec).filter((key) => CONSTRAINT_OPERATORS.includes(key)) : [];
    if (operators.length === 0) {
      if (!pythonEquals(spec, actual)) fail(field, "eq", "does not equal the granted value");
      continue;
    }
    for (const operator of operators) {
      const failure = operatorFailure(operator, (spec as JsonObject)[operator], actual);
      if (failure !== undefined) fail(field, operator, failure);
    }
  }
  return violations;
}
