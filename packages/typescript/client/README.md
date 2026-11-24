# `@asap-protocol/client`

TypeScript client for the ASAP Protocol: discovery, capabilities, Host and Agent JWT identity, SSE streaming, and LLM adapter shims. It runs in browsers and in Node.js 18 or later.

```bash
npm install @asap-protocol/client
```

Full guide: [docs/sdks/typescript.md](https://github.com/asap-protocol/asap-protocol/blob/main/docs/sdks/typescript.md).

## Verify an Agent JWT (server side)

`verifyAgentJwt(token, options)` checks an ASAP Agent JWT (`typ: agent+jwt`, Ed25519) with the same token checks as the Python `verify_agent_jwt`. It never throws for a bad token. It returns `{ ok: false, reason }`, where `reason` is a fixed string (`AgentJwtFailureReason`) with no token content.

```ts
import { InMemoryJtiReplayCache, verifyAgentJwt, type PublicJwk } from "@asap-protocol/client";

interface AgentRecord {
  publicJwk: PublicJwk; // the agent's Ed25519 public key
  hostThumbprint: string;
  status: "active" | "revoked";
}
const agents = new Map<string, AgentRecord>(); // your agent store
const lastUsedAt = new Map<string, number>(); // state that you update per request
const replayCache = new InMemoryJtiReplayCache(); // share one cache per server

export async function verifyRequestToken(token: string) {
  const result = await verifyAgentJwt(token, {
    audience: "https://mcp.example.com/mcp",
    // Runs before the signature check, with the unverified sub and iss: read only, never write.
    resolveAgent: async ({ agentId, hostThumbprint }) => {
      const agent = agents.get(agentId);
      if (agent === undefined) return undefined; // unknown agent
      return { publicKey: agent.publicJwk, usable: agent.status === "active" && agent.hostThumbprint === hostThumbprint };
    },
    replayCache,
  });
  if (result.ok) {
    // Every check passed, so result.agentId is verified: update state here.
    lastUsedAt.set(result.agentId, Date.now());
  }
  return result;
}
```

**The resolver sees unverified values.** `verifyAgentJwt` needs the agent's key to check the signature, so it calls `resolveAgent` first, with the token's `sub` (`agentId`) and `iss` (`hostThumbprint`) before the signature, audience, and expiry checks. A forged token that names a known agent reaches the resolver too. Keep the resolver a read-only lookup. Write state, such as a last-used time for an idle timeout, only after `verifyAgentJwt` returns `ok: true`: the result carries the verified `agentId`, `hostThumbprint`, `capabilities`, and `claims`, so no second lookup is needed.

`publicKey` is a `PublicJwk`. That type needs no DOM lib, and a DOM `JsonWebKey` is assignable to it.

### Differences from the Python verifier

| Check | Python `verify_agent_jwt` | `verifyAgentJwt` |
|-------|---------------------------|------------------|
| `typ`, `alg` (`EdDSA` or `Ed25519`), signature, `exp`, `iat`, `jti`, `aud` | In the function | In the function |
| Lifetime: `exp` at most 120 s (the 60 s TTL plus the 60 s skew) after now and after `iat` | In the function | In the function |
| Agent status (revoked, expired, pending), host revocation, host–agent binding | Read from `HostStore` and `AgentStore` | Your `resolveAgent` callback. Return `undefined` for an unknown agent and `usable: false` for any other refusal |
| Idle-timeout slide (`last_used_at`) | Written to the agent store | Not done. Update your store after `verifyAgentJwt` returns `ok: true`, never in `resolveAgent` (it sees unverified values) |
| Audience | Optional | Required |
| Replay check | Optional cache | Optional `replayCache`. Without it, a replayed token is not detected |
| How long the replay record lasts | The later of the cache window (default 90 s) and the token's `exp` plus 60 s: `verify_agent_jwt` passes `keep_until` to the cache | The later of the cache window (default 90 s) and the token's `exp`: `verifyAgentJwt` passes `exp` to `checkAndRecord` |
| A full in-memory cache | `JtiReplayCache` evicts the records that expire first, so under heavy load a replay can pass (its docstring says so) | `InMemoryJtiReplayCache` never evicts a live record. Expired records make room first; when every record is live, `checkAndRecord` throws `JtiReplayCacheFullError` (with `retryAfterSeconds`), and `verifyAgentJwt` passes the throw on. One agent may hold at most `maxPerPartition` live records (default 1 000) |
| `exp` or `iat` as a numeric string | Accepted | Refused (RFC 7519 NumericDate is a JSON number) |

A throw from `resolveAgent` or `replayCache` is not caught: it means a store failed, not that the token is bad.
