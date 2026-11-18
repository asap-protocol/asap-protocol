# Mastra integration (`@asap-protocol/mastra`)

> **Support tier:** Experimental (Tier 3) — no compatibility promise. See [support tiers](./support-tiers.md).

Expose ASAP **capabilities** as [Mastra](https://mastra.ai/) tools backed by **`@asap-protocol/client`**, optionally wrap a Mastra **`Agent`** with sane defaults, and bridge ASAP **streaming** payloads to Mastra-style text chunks.

**Stack:** **`@asap-protocol/mastra`** peers on **`@mastra/core@^1.5.0`**, **`@asap-protocol/client@^2.3.0`**, and **`zod@^3` or `zod@^4`** (Zod builds the Mastra tool `inputSchema` / `outputSchema` from capability JSON Schema).

!!! note "Package status"

    The adapter ships from the same monorepo as the TypeScript client. Until a maintainer publishes **`@asap-protocol/mastra`** to npm, install from **Git**, **Verdaccio**, or a **workspace `file:`/`workspace:*`** link.

## Requirements

| Runtime / tool | Version |
|----------------|---------|
| Node.js | **≥ 18** |
| `@mastra/core` | **≥ 1.5.0** (CI also checks **`1.5.0`** and **`latest`**) |
| `@asap-protocol/client` | **^2.3.0** |

## Installation

Published (when available):

```bash
npm install @asap-protocol/mastra @asap-protocol/client @mastra/core zod
```

Workspace / monorepo:

```json
{
  "dependencies": {
    "@asap-protocol/mastra": "workspace:*",
    "@asap-protocol/client": "workspace:*",
    "@mastra/core": "^1.5.0",
    "zod": "^3.25.76"
  }
}
```

Ensure your ASAP provider URL and agent identity (**Host/Agent JWT**) match how **`AsapExecuteClient`** is constructed in **`@asap-protocol/client`** (see [TypeScript client](../sdks/typescript.md)).

### Public exports

Import from **`@asap-protocol/mastra`**:

| Export | Role |
|--------|------|
| `asapToolsForMastra(client)` | `async` — build Mastra **`createTool`** instances for each listed capability (calls **`describeCapability`** unless **`inputSchemas`** are pre-supplied) |
| `asapToolsForMastraSync(client)` | Synchronous tools builder when **`inputSchemas` / `outputSchemas`** are already materialized (skips **`describeCapability`**) |
| `createAsapMastraAgent({ client, capabilities, model, … })` | `async` convenience **`Agent`** with ASAP tools wired in (optional **`toolsOptions`**, **`agentId`**, **`name`**, **`instructions`** override defaults) |
| `asapStreamToMastraTextStream(source)` | Map an ASAP-style async iterable (for example SSE-derived parts) into **UTF-8 text chunks** |
| `CreateAsapMastraAgentParams` | Type-only params for **`createAsapMastraAgent`** |
| `FatalError`, `RecoverableError`, `RemoteFatalRPCError`, `RemoteRecoverableRPCError` | Re-exported ASAP errors for consistent handling |

## Basic tool usage

Use **`AsapExecuteClient`** from **`@asap-protocol/client/adapters/shared`** together with **`asapToolsForMastra`** so Mastra receives one tool **per capability**, with deterministic ids from **`capabilityToolKey`** on the server contract.

```ts
import type { AsapExecuteClient } from "@asap-protocol/client/adapters/shared";
import { asapToolsForMastra } from "@asap-protocol/mastra";

declare const client: AsapExecuteClient;
// Populate client.provider URL, JWTs, and capabilities from discovery / escalation flows.

const tools = await asapToolsForMastra(client);
// Pass `tools` into your Mastra Agent or workflow where Mastra expects tool instances.
```

!!! tip "Schema discovery"

    When you omit **`AsapToolsForMastraOptions.inputSchemas`**, the adapter calls **`describeCapability`**
    per capability so Mastra tools get provider JSON Schemas (instead of falling back to permissive records).
    Use **`asapToolsForMastraSync`** only when you already have **`inputSchemas`** / **`outputSchemas`** and
    want zero describe round-trips.

Each tool **`execute`** path calls ASAP **`executeCapability`** with the **`provider`** URL, capability **URN**, **`context`** args, and **optional** JWT / headers from the **`AsapExecuteClient`**.

Typical pitfalls:

- **Missing capability escalation** — the client must list capabilities the model is allowed to call; escalate via **`POST /asap/agent/request-capability`** when the server returns **`capability_not_granted`**.
- **`approval_required`** — surface **approval-required** payloads from **`executeCapability`** / tool **`execute`** in your Mastra error hooks (structured ASAP codes are propagated through **`@asap-protocol/client`**).

## Agent helper

Use **`createAsapMastraAgent`** when you want a single Mastra **`Agent`** preconfigured with ASAP tools and sensible instructions.

```ts
import { createAsapMastraAgent } from "@asap-protocol/mastra";

declare const client: import("@asap-protocol/client/adapters/shared").AsapExecuteClient;

const agent = await createAsapMastraAgent({
  client,
  capabilities: ["urn:asap:cap:demo_echo"],
  model: "openai/gpt-4o-mini",
});

await agent.generate("Say hello via the ASAP echo capability.");
```

- **`capabilities`** restricts which ASAP tools appear on the agent (narrow the surface exposed to the model).
- **`model`** is forwarded to Mastra’s **`Agent`** constructor — use whichever provider id your Mastra stack supports (`openai/gpt-*`, anthropic aliases, Mastra gateways, etc.), or inject a **`@ai-sdk/openai`** / provider instance (see `apps/example-mastra`).
- **`instructions`**, **`name`**, and **`agentId`** are optional Mastra knobs; omit them to use descriptive defaults listing the URNs wired into the agent context.

## Streaming

When you obtain an **async iterable** over ASAP stream payloads (for example SSE chunk objects from **`client.stream` / task streaming**), pass it to **`asapStreamToMastraTextStream`** to yield plain **string chunks** suitable for Mastra **`textStream`** consumers:

```ts
import { asapStreamToMastraTextStream } from "@asap-protocol/mastra";

declare const byteOrEnvelopeStream: AsyncIterable<unknown>;

textLoop: for await (const chunk of asapStreamToMastraTextStream(byteOrEnvelopeStream)) {
  // Forward `chunk` to your UI layer or aggregator.
  console.log(chunk);
}
```

Prefer **consistent encoding** upstream (UTF-8 decoding at the SSE layer) before feeding the iterable; the helper focuses on Mastra ergonomics (**string chunks**) and **bounded async iteration**.

## Errors

The package **`export *` re-exports** select ASAP **`@asap-protocol/client`** error classes so transports and MCP-style handlers line up consistently:

| Condition | Imported from `@asap-protocol/mastra` |
|-----------|----------------------------------------|
| Local fatal failures | **`FatalError`** |
| Local recoverable failures | **`RecoverableError`** |
| Remote JSON-RPC failures (fatal tier) | **`RemoteFatalRPCError`** |
| Remote JSON-RPC failures (recoverable tier) | **`RemoteRecoverableRPCError`** |

Upstream **`capability_not_granted`** and **`approval_required`** responses propagate through **`executeCapability`** / tool **`execute`**; handle them beside the transport guide in [Capability escalation](../capabilities/escalation.md) and your Mastra **`onError`** callbacks.

### Tree-shaking

The package publishes **dual ESM/CJS** bundles. Consume **named imports**.

```bash
pnpm run check:treeshake
```

in **`packages/typescript/mastra`** runs **agadoo** on the built ESM entry to guard side-effect regressions — mirror of the TypeScript SDK CI posture.

## Troubleshooting

| Symptom | What to verify |
|---------|----------------|
| Mastra rejects tool schema | Capability **JSON Schema** must be Mastra-compatible; regenerate from OpenAPI (**`create_from_openapi`**) where possible |
| **`executeCapability`** 401 | **Agent JWT** on **`AsapExecuteClient`** expired or mismatched **`provider`** realm |
| No tools rendered | **`capabilities`** array on **`AsapExecuteClient`** empty — populate from **`listCapabilities`** / discovery |
| Stream yields empty text | Inspect envelope / **`task_stream`** payload shape versus **`asapStreamToMastraTextStream`**; align once streaming bridge parses real SSE payloads |

## Related

- [TypeScript client SDK](../sdks/typescript.md)
- [@mastra/core](https://npmjs.com/package/@mastra/core) (**^1.5.0**)
- [OpenAI Agents SDK adapter](./openai-agents.md) — sibling Lab I npm bridge
- [Workflow connectors](./workflow-connectors.md) · [Microsoft Agent Framework](./microsoft-agent-framework.md) · [NeMo Agent Toolkit](./nemo-agent-toolkit.md) — Adapter Lab II
