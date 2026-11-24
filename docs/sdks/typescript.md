# TypeScript client SDK (`@asap-protocol/client`)

Official browser- and Node-compatible client for ASAP **discovery**, **capabilities**, **Host/Agent JWT identity**, **SSE streaming**, and **LLM adapter shims** (Vercel AI SDK, OpenAI, Anthropic).

**Requirements:** Node.js **≥ 18** (Web Crypto `Ed25519` where available; [`@noble/ed25519`](https://www.npmjs.com/package/@noble/ed25519) fallback in older runtimes — see package tests).

## Installation

From npm (after release):

```bash
npm install @asap-protocol/client
```

In this monorepo, depend on the workspace package:

```json
{
  "dependencies": {
    "@asap-protocol/client": "workspace:*"
  }
}
```

Optional peer dependencies (install only what you use):

| Peer | Purpose |
|------|---------|
| `ai` | Vercel AI SDK adapter (`@asap-protocol/client/adapters/vercel-ai`) |
| `zod` | Required by the Vercel adapter’s schema helpers |
| `keytar` | macOS/Windows/Linux keychain storage (Node/desktop only) |

Tree-shake adapters by importing **subpaths** (see [Adapters](#adapters)) so unused providers stay out of your bundle.

## Identity and storage

Hosts and agents use **Ed25519** keys and short-lived **JWTs** (`jose`). Keys must be persisted with a [`Storage`](https://github.com/asap-protocol/asap-protocol/blob/main/packages/typescript/client/src/storage-local.ts)-compatible backend.

```ts
import {
  createHost,
  createAgent,
  MemoryStorage,
  LocalStorage,
} from "@asap-protocol/client";

const storage = typeof localStorage !== "undefined"
  ? new LocalStorage("asap:")
  : new MemoryStorage();

const host = await createHost({ storage });
const agent = await createAgent(host, { mode: "delegated" });
// Use host.agentJwt / agent metadata as required by your gateway.
```

- **`createHost` / `createAgent`** — generate keys, persist, issue Host and Agent JWTs.
- **`resumeHost` / `resumeAgent`** — reload identity from storage after restart.

### Storage selection

| Backend | Import | When to use |
|---------|--------|-------------|
| In-memory | `MemoryStorage` | Tests, ephemeral demos |
| Browser `localStorage` | `LocalStorage` | Default for web apps |
| Node filesystem | `FileStorage` from `@asap-protocol/client/storage-node` | Servers, CLI |
| OS keychain | `KeychainStorage` from `@asap-protocol/client/storage-node` | Desktop apps; requires optional `keytar` |

Always import `@asap-protocol/client/storage-node` **only in Node/desktop** builds so bundlers do not pull `node:fs` or native addons into browser chunks.

## Agent JWT verification (server side)

A server that accepts ASAP Agent JWTs checks them with `verifyAgentJwt` (added in 2.5.0). It runs the token checks of the Python `verify_agent_jwt`: `typ: agent+jwt`, `alg` `EdDSA` or its RFC 9864 name `Ed25519`, an Ed25519 signature, `exp` (required), a lifetime of at most 120 seconds (the 60-second TTL plus the 60-second clock skew, counted from now and from `iat`), `iat` (at most 60 seconds ahead), `jti`, and `aud`.

```ts
import { InMemoryJtiReplayCache, verifyAgentJwt } from "@asap-protocol/client";

const replayCache = new InMemoryJtiReplayCache();
const result = await verifyAgentJwt(token, {
  audience: "https://mcp.example.com/mcp", // required
  resolveAgent: async ({ agentId, hostThumbprint }) => lookUpAgent(agentId, hostThumbprint),
  replayCache, // an Agent JWT is single-use
});
if (!result.ok) {
  // result.reason is a fixed AgentJwtFailureReason string, safe to return to the caller
}
```

- **`resolveAgent`** gets the token's `sub` (`agentId`) and `iss` (`hostThumbprint`). It returns `undefined` for an unknown agent, or `{ publicKey, usable }`: the agent's Ed25519 public JWK, and `usable: false` when the agent is revoked, expired, or pending, when its host is revoked, or when the agent does not belong to that host. The Python function reads these facts from its host and agent stores; here your resolver supplies them.
- **`replayCache`** records each `jti` under the agent id, with the token's `exp` as the third argument of `checkAndRecord`; a cache must keep the record at least until then. `InMemoryJtiReplayCache` keeps it for the later of its window (default 90 seconds) and `exp`. It never evicts a live record: when it is full (`maxSize`, default 10 000, or `maxPerPartition` for one agent, default 1 000), it throws `JtiReplayCacheFullError`, which `verifyAgentJwt` passes on. Treat that as a temporary failure (for example HTTP 503 with `Retry-After: retryAfterSeconds`), not as a bad token. `InMemoryJtiReplayCache` serves one process; implement `JtiReplayCache` on a shared store when several instances serve the same audience. Without a cache, a replayed token is not detected.
- **`nowSeconds`** replaces the clock (seconds since the epoch), for tests.
- A bad token never throws. A throw from `resolveAgent` or `replayCache` propagates, so a store failure is not reported as a bad token.

The package README lists every difference from the Python verifier.

To protect an HTTP MCP server, you do not call `verifyAgentJwt` yourself: `@asap-protocol/mcp-auth` calls it on each request. See [HTTP MCP servers in TypeScript](../adapters/mcp-auth-bridge.md#http-mcp-servers-in-typescript).

## Discovery and capabilities

```ts
import {
  discoverProvider,
  listCapabilities,
  describeCapability,
  executeCapability,
} from "@asap-protocol/client";

const manifest = await discoverProvider("https://agent.example.com");
const provider = new URL(manifest.endpoints.asap);

const { capabilities } = await listCapabilities(provider, { agentJwt, fetch });
const detail = await describeCapability(provider, "my.skill", { agentJwt });
const result = await executeCapability(provider, "my.skill", { foo: "bar" }, {
  agentJwt,
});
```

Use [`connectAgent`](https://github.com/asap-protocol/asap-protocol/blob/main/packages/typescript/client/src/connection.ts) for registration, approval, disconnect, and capability requests against a live provider.

## Streaming (SSE)

```ts
import { createAsapStreamClient } from "@asap-protocol/client";

const client = createAsapStreamClient({
  baseUrl: provider,
  agentJwt,
  fetch,
});

// Build your task envelope (e.g. TaskRequest) then:
for await (const chunk of client.stream(taskEnvelope)) {
  // Typed Envelope<TaskStream> until payload.final === true
}
```

Streaming uses `fetch` + `ReadableStream` and parses **SSE** into typed **`Envelope<TaskStream>`** payloads. Lower-level access: **`streamTaskStreamEnvelopes`** when you already have a full `/asap/stream` URL.

## Adapters

Import from **subpaths** to keep bundles small:

### Vercel AI SDK (`ai`)

```ts
import { asapToolsForVercel } from "@asap-protocol/client/adapters/vercel-ai";
import { streamText } from "ai";

const tools = asapToolsForVercel({
  provider,
  capabilities: ["echo"],
  agentJwt,
  fetch,
});

await streamText({ model, tools, prompt });
```

Peer deps: **`ai`**, **`zod`** (supported majors per `package.json`).

### OpenAI (`ChatCompletionTool`)

```ts
import { asapToolsForOpenAI } from "@asap-protocol/client/adapters/openai";

const tools = asapToolsForOpenAI({
  provider,
  capabilities,
  agentJwt,
  fetch,
});
```

### Anthropic (Messages API tools)

```ts
import { asapToolsForAnthropic } from "@asap-protocol/client/adapters/anthropic";

const tools = asapToolsForAnthropic({
  provider,
  capabilities,
  agentJwt,
  fetch,
});
```

## Error handling and retries

The SDK maps JSON-RPC errors into typed classes (aligned with [ADR-012](../adr/ADR-012-error-taxonomy.md) concepts):

- **`RecoverableError`** / **`RemoteRecoverableRPCError`** — may include `retryAfterMs`; safe to retry with backoff.
- **`FatalError`** / **`RemoteFatalRPCError`** — do not blindly retry.

Use **`callWithRecoverableRetry`** for call sites that should automatically retry recoverable failures using `retry_after_ms` when present (Python client parity). Optionally pass **`fallbackBackoffMs`** so recoverable errors **without** `retryAfterMs` still retry using bounded exponential backoff (capped at 60 seconds per wait).

```ts
import { callWithRecoverableRetry } from "@asap-protocol/client";

await callWithRecoverableRetry(() => doRpcCall(), { maxRetries: 3 });
await callWithRecoverableRetry(() => doRpcCall(), { fallbackBackoffMs: 500 });
```

For wire-level JSON helpers, see **`remoteRpcErrorFromJson`** and related exports in [`errors.ts`](https://github.com/asap-protocol/asap-protocol/blob/main/packages/typescript/client/src/errors.ts).

## Security

- JS heaps cannot reliably wipe key material; the SDK clears the JWK `d` field after `importKey` as minor hardening.
- If you use `fallbackBackoffMs`, size retries to your gateway limits.

## Reference app

The **[`apps/example-nextjs`](https://github.com/asap-protocol/asap-protocol/tree/main/apps/example-nextjs)** App Router demo registers a host in **`LocalStorage`**, connects an agent, and wires **`asapToolsForVercel`** into a chat UI. Copy patterns from `.env.example` for provider URL and API keys.

## See also

- [TypeScript consumer starter](../../examples/starters/typescript-consumer/) — thin Node identity smoke (`examples/starters/typescript-consumer/`)
- [Transport overview](../transport.md) — HTTP/SSE endpoints and versioning
- [Error handling (Python-centric)](../error-handling.md) — taxonomy and JSON-RPC mapping
- [Vercel AI SDK (Python gateway)](../guides/vercel-ai-sdk.md) — complementary server-side tools router
- [Build for agents](../guides/build-for-agents.md) — Dist Loop onboarding guide
