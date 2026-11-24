/**
 * The demo ASAP identity of the example: one host and one agent, in memory,
 * created at each start. The agent holds one grant on the protected tool,
 * with one constraint, so the example shows both grant errors.
 */
import { createAgent, createHost, MemoryStorage, type AgentKeyResolver } from "@asap-protocol/client";
import { staticAgentGrants, type AgentGrantResolver } from "@asap-protocol/mcp-auth";

export const PROTECTED_CAPABILITY = "secure_action";
/** The one `action` value that the demo grant forbids (same name as the Python stdio example). */
export const FORBIDDEN_ACTION = "forbidden-action";

export interface DemoIdentity {
  readonly agentId: string;
  readonly resolveAgent: AgentKeyResolver;
  readonly agentGrants: AgentGrantResolver;
  /** A fresh Agent JWT: it lives 60 seconds and is single-use, so mint one per request. */
  mintAgentJwt(audience: string, capabilities?: string[]): Promise<string>;
}

export async function createDemoIdentity(): Promise<DemoIdentity> {
  const agent = await createAgent(await createHost({ storage: new MemoryStorage() }), { mode: "delegated" });
  return {
    agentId: agent.agentId,
    // The resolver, not the package, checks that the agent belongs to the host named in the token.
    resolveAgent: async ({ agentId, hostThumbprint }) =>
      agentId === agent.agentId ? { publicKey: agent.publicJwk, usable: hostThumbprint === agent.hostThumbprint } : undefined,
    agentGrants: staticAgentGrants({
      [agent.agentId]: [{ capability: PROTECTED_CAPABILITY, constraints: { action: { not_in: [FORBIDDEN_ACTION] } } }],
    }),
    mintAgentJwt: (audience, capabilities = [PROTECTED_CAPABILITY]) => agent.signAgentJwt({ aud: audience, capabilities }),
  };
}
