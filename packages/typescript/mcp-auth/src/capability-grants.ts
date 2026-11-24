import { CONSTRAINT_OPERATORS, isJsonObject, pythonEquals, type ConstraintSet } from "./constraints.js";

/** One capability that a principal may call, with the constraints its call arguments must meet. */
export interface CapabilityGrant {
  readonly capability: string;
  /** `null` (or absent) when the grant has no constraints. */
  readonly constraints?: ConstraintSet | null;
}

/**
 * One rule of an issuer's static grant mapping (COMPAT-002): a `scope`
 * token, or a top-level `claim` with an exact string `value`, gives one
 * `capability` with optional `constraints`. Same fields as Python `ExternalGrantRule`.
 */
export interface ExternalGrantRule {
  readonly scope?: string;
  readonly claim?: string;
  readonly value?: string;
  readonly capability: string;
  readonly constraints?: ConstraintSet | null;
}

const RULE_KEYS: readonly string[] = ["scope", "claim", "value", "capability", "constraints"];
const SCOPE_CLAIM = "scope";
// RFC 6749 §3.3 scope-token; the SDK's scope challenge refuses any other scope.
const SCOPE_TOKEN_PATTERN = /^[\x21\x23-\x5B\x5D-\x7E]+$/;

function isSet(value: unknown): boolean {
  return value !== undefined && value !== null;
}

function constraintSpecProblem(spec: unknown): string | undefined {
  if (!isJsonObject(spec)) return undefined;
  const keys = Object.keys(spec);
  if (keys.length === 0 || keys.some((key) => !CONSTRAINT_OPERATORS.includes(key))) {
    return `an object may hold only ${CONSTRAINT_OPERATORS.join(", ")}, got keys [${keys.join(", ")}]`;
  }
  for (const [operator, expected] of Object.entries(spec)) {
    const isBound = operator === "max" || operator === "min";
    if (isBound && !(typeof expected === "number" && Number.isFinite(expected))) return `${operator} needs a finite number, got ${String(expected)}`;
    if (!isBound && !Array.isArray(expected)) return `${operator} needs a list, got ${JSON.stringify(expected)}`;
  }
  return undefined;
}

/** Python `_check_constraint_spec`: refuse what `validateConstraints` would ignore, misread, or fail closed on. */
function constraintsProblem(constraints: unknown): string | undefined {
  if (!isSet(constraints)) return undefined;
  if (!isJsonObject(constraints)) return "constraints must be an object";
  for (const [field, spec] of Object.entries(constraints)) {
    const problem = constraintSpecProblem(spec);
    if (problem !== undefined) return `constraint '${field}': ${problem}`;
  }
  return undefined;
}

function matcherIsValid(rule: ExternalGrantRule): boolean {
  if (isSet(rule.scope)) {
    return !isSet(rule.claim) && !isSet(rule.value) && typeof rule.scope === "string" && SCOPE_TOKEN_PATTERN.test(rule.scope);
  }
  return typeof rule.claim === "string" && rule.claim !== "" && rule.claim !== SCOPE_CLAIM && typeof rule.value === "string";
}

function ruleProblem(rule: ExternalGrantRule): string | undefined {
  const unknownKeys = Object.keys(rule).filter((key) => !RULE_KEYS.includes(key));
  if (unknownKeys.length > 0) return `has unknown keys [${unknownKeys.join(", ")}]`;
  if (!matcherIsValid(rule)) {
    return `needs either one RFC 6749 scope token, or a claim other than 'scope' with a string value; got scope=${JSON.stringify(rule.scope)}, claim=${JSON.stringify(rule.claim)}, value=${JSON.stringify(rule.value)}`;
  }
  return constraintsProblem(rule.constraints);
}

/**
 * Check an issuer's grant rules once, at config time. Throws a `TypeError`
 * that names the rule for a malformed rule (the checks of Python
 * `ExternalGrantRule`, plus the RFC 6749 scope-token grammar), and for two
 * rules that give one capability different constraints (Python
 * `ensure_unambiguous_grant_rules`: the result would depend on rule order).
 *
 * @example
 * ```ts
 * assertValidGrantRules([{ scope: "files:read", capability: "file.read", constraints: { path: { in: ["/srv/public"] } } }]);
 * ```
 */
export function assertValidGrantRules(rules: readonly ExternalGrantRule[]): void {
  const constraintsByCapability = new Map<string, unknown>();
  for (const rule of rules) {
    const capability: unknown = rule.capability;
    const problem = typeof capability === "string" && capability !== "" ? ruleProblem(rule) : "needs a non-empty capability";
    if (problem !== undefined) throw new TypeError(`grant rule for ${JSON.stringify(capability)} ${problem}`);
    const constraints = rule.constraints ?? null;
    const known = constraintsByCapability.has(rule.capability) ? constraintsByCapability.get(rule.capability) : constraints;
    if (!pythonEquals(known, constraints)) {
      throw new TypeError(`grant rules give capability '${rule.capability}' two constraint sets: ${JSON.stringify(known)} and ${JSON.stringify(constraints)}`);
    }
    constraintsByCapability.set(rule.capability, constraints);
  }
}

function scopeTokens(claims: Readonly<Record<string, unknown>>): ReadonlySet<string> {
  const scope = Object.hasOwn(claims, SCOPE_CLAIM) ? claims[SCOPE_CLAIM] : undefined;
  return new Set(typeof scope === "string" ? scope.split(" ").filter((token) => token !== "") : []);
}

function ruleMatches(rule: ExternalGrantRule, claims: Readonly<Record<string, unknown>>, scopes: ReadonlySet<string>): boolean {
  if (isSet(rule.scope)) return scopes.has(rule.scope as string);
  const actual = rule.claim !== undefined && Object.hasOwn(claims, rule.claim) ? claims[rule.claim] : undefined;
  if (typeof actual === "string") return actual === rule.value;
  return Array.isArray(actual) && actual.some((item) => typeof item === "string" && item === rule.value);
}

/**
 * Port of Python `map_external_grants`: the grants that `rules` give to the
 * validated claims of an external token, one per capability, sorted by
 * capability. Deny by default: an unmapped scope or claim gives nothing. A
 * `scope` rule matches one token of the `scope` claim (a string split on
 * single spaces); a `claim` rule matches a top-level claim that is the exact
 * string `value` or a list holding it, with no type conversion. Check the
 * rules first with `assertValidGrantRules`. Shared vectors:
 * `tests/fixtures/interop/grant-mapping.json`.
 *
 * @example
 * ```ts
 * mapExternalGrants({ scope: "files:read admin" }, [{ scope: "files:read", capability: "file.read" }]);
 * // [{ capability: "file.read", constraints: null }]
 * ```
 */
export function mapExternalGrants(claims: Readonly<Record<string, unknown>>, rules: readonly ExternalGrantRule[]): CapabilityGrant[] {
  const scopes = scopeTokens(claims);
  const granted = new Map<string, CapabilityGrant>();
  for (const rule of rules) {
    if (!granted.has(rule.capability) && ruleMatches(rule, claims, scopes)) {
      granted.set(rule.capability, { capability: rule.capability, constraints: rule.constraints ?? null });
    }
  }
  return [...granted.keys()].sort().map((capability) => granted.get(capability) as CapabilityGrant);
}
