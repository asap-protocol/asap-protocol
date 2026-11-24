# `@asap-protocol/client`

TypeScript client for the ASAP Protocol: discovery, capabilities, Host and Agent JWT identity, SSE streaming, and LLM adapter shims. It runs in browsers and in Node.js 18 or later.

```bash
npm install @asap-protocol/client
```

Full guide: [docs/sdks/typescript.md](https://github.com/asap-protocol/asap-protocol/blob/main/docs/sdks/typescript.md).

## Verify an Agent JWT (server side)

`verifyAgentJwt(token, options)` checks an ASAP Agent JWT (`typ: agent+jwt`, Ed25519) with the same token checks as the Python `verify_agent_jwt`. It never throws for a bad token. It returns `{ ok: false, reason }`, where `reason` is a fixed string (`AgentJwtFailureReason`) with no token content.

```ts
import { InMemoryJtiReplayCache, verifyAgentJwt } from "@asap-protocol/client";

const replayCache = new InMemoryJtiReplayCache(); // share one cache per server
const result = await verifyAgentJwt(token, {
  audience: "https://mcp.example.com/mcp",
  resolveAgent: async ({ agentId, hostThumbprint }) => {
    const agent = await agents.get(agentId);
    if (agent === undefined) return undefined; // unknown agent
    return { publicKey: agent.publicJwk, usable: agent.status === "active" && agent.hostThumbprint === hostThumbprint };
  },
  replayCache,
});
if (result.ok) console.log(result.agentId, result.capabilities);
```

### Differences from the Python verifier

| Check | Python `verify_agent_jwt` | `verifyAgentJwt` |
|-------|---------------------------|------------------|
| `typ`, `alg` (`EdDSA` or `Ed25519`), signature, `exp`, `iat`, `jti`, `aud` | In the function | In the function |
| Agent status (revoked, expired, pending), host revocation, host–agent binding | Read from `HostStore` and `AgentStore` | Your `resolveAgent` callback. Return `undefined` for an unknown agent and `usable: false` for any other refusal |
| Idle-timeout slide (`last_used_at`) | Written to the agent store | Not done. Update your store in the resolver if you need it |
| Audience | Optional | Required |
| Replay check | Optional cache | Optional `replayCache`. Without it, a replayed token is not detected |
| `exp` or `iat` as a numeric string | Accepted | Refused (RFC 7519 NumericDate is a JSON number) |

A throw from `resolveAgent` or `replayCache` is not caught: it means a store failed, not that the token is bad.
