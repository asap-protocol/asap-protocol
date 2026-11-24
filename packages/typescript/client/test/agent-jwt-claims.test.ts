import { describe, expect, it } from "vitest";

import {
  AgentJwtFailureReason,
  capabilitiesOf,
  standardClaimFailure,
  type AgentJwtClaims,
} from "../src/agent-jwt-claims.js";

const NOW = 1_800_000_000;
const AUDIENCE = "https://mcp.example.com/mcp";
const valid: AgentJwtClaims = { aud: AUDIENCE, iat: NOW, exp: NOW + 60, jti: "jwt_1" };

describe("standardClaimFailure (port of Python _validate_standard_claims)", () => {
  it.each<[string, AgentJwtClaims]>([
    ["valid claims", valid],
    ["an iat 60 s ahead (the allowed skew)", { ...valid, iat: NOW + 60 }],
    ["no iat", { ...valid, iat: undefined }],
    ["an aud list that contains the audience", { ...valid, aud: ["https://a.example", AUDIENCE] }],
    // The limit is the 60 s TTL plus the 60 s skew, for both checks.
    ["an exp 120 s ahead from a clock 60 s fast", { ...valid, iat: NOW + 60, exp: NOW + 120 }],
    ["an exp 120 s after iat", { ...valid, exp: NOW + 120 }],
  ])("accepts %s", (_case, claims) => {
    expect(standardClaimFailure(claims, NOW, AUDIENCE)).toBeUndefined();
  });

  it.each<[string, AgentJwtClaims, AgentJwtFailureReason]>([
    ["no exp", { ...valid, exp: undefined }, AgentJwtFailureReason.Expired],
    ["exp equal to now (exp is exclusive)", { ...valid, exp: NOW }, AgentJwtFailureReason.Expired],
    ["a numeric-string exp", { ...valid, exp: String(NOW + 60) }, AgentJwtFailureReason.Expired],
    ["an exp an hour ahead", { ...valid, exp: NOW + 3600 }, AgentJwtFailureReason.LifetimeTooLong],
    ["an exp 121 s ahead, with no iat", { ...valid, iat: undefined, exp: NOW + 121 }, AgentJwtFailureReason.LifetimeTooLong],
    ["an exp 121 s after iat, though only 21 s ahead", { ...valid, iat: NOW - 100, exp: NOW + 21 }, AgentJwtFailureReason.LifetimeTooLong],
    ["an iat more than 60 s ahead", { ...valid, iat: NOW + 61 }, AgentJwtFailureReason.IatInFuture],
    ["a non-numeric iat", { ...valid, iat: "yesterday" }, AgentJwtFailureReason.IatInFuture],
    ["no jti", { ...valid, jti: undefined }, AgentJwtFailureReason.MissingJti],
    ["a blank jti", { ...valid, jti: " " }, AgentJwtFailureReason.MissingJti],
    ["no aud", { ...valid, aud: undefined }, AgentJwtFailureReason.AudienceMismatch],
    ["another aud", { ...valid, aud: "https://other.example" }, AgentJwtFailureReason.AudienceMismatch],
    ["an aud list of non-strings", { ...valid, aud: [7, null] }, AgentJwtFailureReason.AudienceMismatch],
  ])("rejects %s", (_case, claims, reason) => {
    expect(standardClaimFailure(claims, NOW, AUDIENCE)).toBe(reason);
  });

  it("checks exp before aud, as Python does", () => {
    const claims = { ...valid, exp: NOW - 1, aud: "https://other.example" };

    expect(standardClaimFailure(claims, NOW, AUDIENCE)).toBe(AgentJwtFailureReason.Expired);
  });

  it("accepts a token for any one of several expected audiences", () => {
    expect(standardClaimFailure(valid, NOW, ["https://other.example", AUDIENCE])).toBeUndefined();
  });
});

describe("capabilitiesOf", () => {
  it("keeps only the string entries of the claim", () => {
    expect(capabilitiesOf({ capabilities: ["web_search", 7, null, "echo"] })).toEqual(["web_search", "echo"]);
  });

  it("returns an empty list when the claim is absent or not a list", () => {
    expect(capabilitiesOf({})).toEqual([]);
    expect(capabilitiesOf({ capabilities: "web_search" })).toEqual([]);
  });
});
