/**
 * Compliance mode (test only). It is off by default and only the
 * `--compliance` command-line flag turns it on; no environment variable does.
 * It serves `POST /compliance/probe-tokens`, which mints a fresh set of valid
 * probe tokens on each call, so a remote checker can run its checks again and
 * again. Anyone who can reach that path can call the protected tool, so never
 * run this mode where an untrusted client can reach the server.
 */
import type { DemoIdentity } from "./demo-identity.js";
import { FORBIDDEN_ACTION } from "./demo-identity.js";
import { EXTERNAL_SCOPE, type TestIssuer } from "./test-issuer.js";

export const COMPLIANCE_PROBE_PATH = "/compliance/probe-tokens";

/** What the probe path returns. Snake case, for the Python harness. Agent JWTs are single-use. */
export interface ComplianceProbeTokens {
  readonly profile: "mcp-auth-http";
  readonly test_only: true;
  readonly mcp_url: string;
  readonly protected_tool: string;
  readonly allowed_arguments: Record<string, unknown>;
  readonly constraint_violation_arguments: Record<string, unknown>;
  readonly agent_jwt: { readonly valid: string; readonly out_of_grant: string; readonly constraint_violation: string };
  readonly external: { readonly valid: string; readonly out_of_grant: string };
}

export async function mintComplianceProbeTokens(mcpUrl: URL, protectedTool: string, demo: DemoIdentity, issuer: TestIssuer): Promise<ComplianceProbeTokens> {
  const audience = mcpUrl.href;
  return {
    profile: "mcp-auth-http",
    test_only: true,
    mcp_url: audience,
    protected_tool: protectedTool,
    allowed_arguments: { action: "deploy" },
    constraint_violation_arguments: { action: FORBIDDEN_ACTION },
    agent_jwt: {
      valid: await demo.mintAgentJwt(audience),
      out_of_grant: await demo.mintAgentJwt(audience, ["unrelated_capability"]),
      constraint_violation: await demo.mintAgentJwt(audience),
    },
    external: {
      valid: await issuer.mint(audience, EXTERNAL_SCOPE),
      out_of_grant: await issuer.mint(audience, "unrelated:scope"),
    },
  };
}
