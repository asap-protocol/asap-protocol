# OpenAI Agents SDK integration (`@asap-protocol/openai-agents`)

> **Support tier:** Experimental (Tier 3) — no compatibility promise. See [support tiers](./support-tiers.md).

Expose ASAP **capabilities** as [**OpenAI Agents SDK**](https://openai.github.io/openai-agents-js/) **`tool()`** definitions backed by **`@asap-protocol/client`**, optionally wrap an **`Agent`** you attach as a **handoff** target against an ASAP gateway, and bridge ASAP **`task_stream`** payloads into incremental assistant **text deltas**.

!!! warning "Not `@asap-protocol/client/adapters/openai`"

    `@asap-protocol/client/adapters/openai` generates **static `ChatCompletionTool[]`** for the raw OpenAI **Chat Completions HTTP API** (`openai` npm package).

    **`@asap-protocol/openai-agents`** targets **`@openai/agents`** (`Agent`, `tool()`, handoffs, `run()`). Different runtime — **both coexist**.

## Requirements

| Runtime / tool | Version |
|----------------|---------|
| Node.js | **≥ 18** |
| `@openai/agents` | **`^0.11.0`** (CI exercises **`latest`** + pinned **`0.11.4`**) |
| `@asap-protocol/client` | **`^2.3.0`** |
| `zod` | **`^4.1.8`** (matches `@openai/agents` peer; Zod 3 is not supported by this package) |

## Installation

Published (when available):

```bash
npm install @asap-protocol/openai-agents @asap-protocol/client @openai/agents zod
```

Workspace / monorepo:

```json
{
  "dependencies": {
    "@asap-protocol/openai-agents": "workspace:*",
    "@asap-protocol/client": "workspace:*",
    "@openai/agents": "^0.11.4",
    "zod": "^4.4.2"
  }
}
```

Configure **`AsapExecuteClient`** (`provider`, **`capabilities[]`**, optional JWT / fetch) exactly like other adapters — see [TypeScript client](../sdks/typescript.md).

### Public exports (`@asap-protocol/openai-agents`)

| Export | Role |
|--------|------|
| `asapToolsForOpenAIAgents(client)` | `async` — build **`tool()`** instances (calls **`describeCapability`** unless **`inputSchemas`** are pre-supplied) |
| `asapToolsForOpenAIAgentsSync(client)` | Same tools **without** describe round-trips when **`inputSchemas`** are provided |
| `asapAsRemoteAgent(client, providerUrl, options?)` | Returns an OpenAI **`Agent`** wired with ASAP tools; on **`agent_start`** POSTs a real **`task.request`** via JSON-RPC **`asap.send`** and stores the draft on **`RunContext.context.lastAsapHandoffEnvelope`** |
| `sendAsapEnvelope(provider, envelope, options?)` | Lower-level **`POST /asap`** helper used by handoffs (JSON-RPC **`asap.send`**) |
| `draftTaskRequestEnvelopeForRemoteAgent({ mode, providerUrl, turnInput })` | Stable envelope-shaped draft carrying **`extensions.asap_agent_mode`** (`delegated` \| `autonomous`) |
| `zodFromJsonSchema(schema)` | Shared JSON Schema → Zod helper for offline validation / tooling |
| `asapStreamToOpenAIAgentsTextStream(source)` | Map ASAP **`task_stream`** envelopes → UTF-8 chunks |
| `asapStreamToOpenAIAgentsRunStreamChunks(source)` | Same chunks wrapped as **`{ type: "text_delta", text }`** |
| ASAP RPC error re-exports + **`ApprovalRequiredError`**, **`CapabilityNotGrantedError`** | Approval / escalation symmetry with Mastra adapter |

!!! warning "Pre-1.0 `@openai/agents` API drift"

    **`@openai/agents` is pre-1.0.** Minor/patch releases may change `Agent` constructor options, `tool()` parameter shapes, or default tool error handling. CI exercises **`latest`** and pinned **`0.11.4`**; pin your app dependency and re-run compliance after upgrades.

## Basic capability tools

```typescript
import { Agent, run, setDefaultOpenAIKey } from "@openai/agents";
import type { AsapExecuteClient } from "@asap-protocol/client";
import { asapToolsForOpenAIAgents } from "@asap-protocol/openai-agents";

declare const client: AsapExecuteClient;

setDefaultOpenAIKey(process.env.OPENAI_API_KEY!);

const tools = await asapToolsForOpenAIAgents(client);
const agent = new Agent({
  name: "asap-demo",
  instructions: "Use ASAP capability tools when they help answer the user.",
  model: "gpt-4o-mini",
  tools: [...tools],
});

await run(agent, "Echo hello via ASAP capabilities.");
```

Each tool **`execute`** path calls **`executeCapability(provider, capabilityId, args, { fetch, agentJwt })`** — **not** a method on `client`.

!!! tip "`invokeFunctionTool` and typed errors"

    OpenAI Agents wraps tool failures unless **`errorFunction` is disabled**. This adapter sets **`errorFunction: null`** so **`ApprovalRequiredError`**, **`CapabilityNotGrantedError`**, and ASAP **`RecoverableError`** propagate to host code (`packages/typescript/openai-agents` tests rely on **`invokeFunctionTool`** for unary execution).

### Schema modes

When describe JSON Schema lists **`properties`**, parameters compile to **strict Zod objects** (`strict: true`) — matching SDK requirements.

Fallback **open-object** schemas compile to **JSON Schema parameters** (`strict: false`). Rare ASAP schemas still route through permissive records — mirror Mastra limitations.

## Handoffs (`asapAsRemoteAgent`)

```typescript
import { asapAsRemoteAgent } from "@asap-protocol/openai-agents";
import type { AsapExecuteClient } from "@asap-protocol/client";

declare const client: AsapExecuteClient;

const specialist = await asapAsRemoteAgent(client, client.provider, {
  mode: "delegated",
});

// Pass `specialist` via Agent `handoffs: [...]` from your orchestrator agent.
```

On **`agent_start`**, the adapter:

1. Sets **`RunContext.context.lastAsapHandoffEnvelope`** from **`draftTaskRequestEnvelopeForRemoteAgent`** (`extensions.asap_agent_mode`, serialized turn input).
2. **POSTs** that envelope to **`providerUrl`** via JSON-RPC **`asap.send`** at **`/asap`** (same wire shape as the Python **`ASAPClient.send`**).

**Agent JWT scope:** **`client.agentJwt`** is only forwarded when **`providerUrl`** matches **`client.provider`** (same origin **and** pathname). For cross-provider handoffs, pass **`remoteAgentJwt`** in options — otherwise **`asapAsRemoteAgent`** throws before any HTTP call.

## Capability escalation (`capability_not_granted`)

Provide **`requestCapability`** identical to Mastra:

```typescript
await asapToolsForOpenAIAgents(client, {
  async requestCapability(required) {
    await fetch(`/approval/${encodeURIComponent(required)}`, {
      method: "POST",
      // Protect approval endpoints with session auth and CSRF tokens as required by your service.
    });
  },
});
```

!!! note "Approval endpoint security"

    **`requestCapability`** runs in your host process — encode capability IDs in URLs (`encodeURIComponent`) and ensure approval routes require authenticated sessions and CSRF protection where applicable.

Thrown **`CapabilityNotGrantedError`** exposes **`.requestCapability()`** so orchestrators can retry after grants.

## Streaming

Combine **`createAsapStreamClient`** / **`streamTaskStreamEnvelopes`** (`@asap-protocol/client/streaming`) with:

```typescript
import { asapStreamToOpenAIAgentsRunStreamChunks } from "@asap-protocol/openai-agents";

for await (const delta of asapStreamToOpenAIAgentsRunStreamChunks(streamClient.stream(envelope))) {
  process.stdout.write(delta.text);
}
```

This mirrors Mastra **`asapStreamToMastraTextStream`** but emits **`OpenAIAgentsStreamTextDelta`** records you can merge into UI streams.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| **`Strict mode is required for Zod parameters`** | Upgrade **`@openai/agents`** ≥ **0.11** — older helpers assumed looser typing. Empty capability schemas automatically downgrade to JSON-schema tools. |
| Tool calls succeed but HTTP hits wrong host | **`asapAsRemoteAgent`** merges **`providerUrl`** into **`client.provider`** — verify trailing slashes / TLS. |
| **`client.agentJwt is scoped to …`** on handoff | **`providerUrl`** differs from **`client.provider`** — supply **`remoteAgentJwt`** scoped to the remote gateway. |
| Streaming stalls | Confirm SSE **`Accept: text/event-stream`** gateway compatibility (`streamTaskStreamEnvelopes`). |

Runnable CLI demo: [`apps/example-openai-agents/README.md`](https://github.com/asap-protocol/asap-protocol/tree/main/apps/example-openai-agents).

Compliance Harness: `pnpm --filter example-openai-agents run compliance`.

## Related

- [TypeScript client SDK](../sdks/typescript.md)
- [Mastra adapter](./mastra.md) — sibling Lab I npm bridge
- [Workflow connectors](./workflow-connectors.md) · [Microsoft Agent Framework](./microsoft-agent-framework.md) · [NeMo Agent Toolkit](./nemo-agent-toolkit.md) — Adapter Lab II
