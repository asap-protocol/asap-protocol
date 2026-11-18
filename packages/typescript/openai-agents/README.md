# `@asap-protocol/openai-agents`

> **Support tier:** Experimental (Tier 3) — no compatibility promise. See [support tiers](https://github.com/asap-protocol/asap-protocol/blob/main/docs/integrations/support-tiers.md).

## ⚠️ This is **not** `@asap-protocol/client/adapters/openai`

[`@asap-protocol/client/adapters/openai`](https://github.com/asap-protocol/asap-protocol/tree/main/packages/typescript/client/src/adapters/openai.ts) exposes **`asapToolsForOpenAI`**, producing **static `ChatCompletionTool[]`** for the **OpenAI Chat Completions API** (`openai` npm package, no Agents runtime).

**`@asap-protocol/openai-agents`** integrates with the **`@openai/agents` SDK** (`Agent`, `tool()`, handoffs, `run()`). Different consumer and lifecycle — **both packages coexist**.

## Overview

Maps ASAP **capabilities** to OpenAI Agents **`tool()`** definitions (using **`executeCapability`** from **`@asap-protocol/client`**), optionally wraps an **`Agent`** intended as a **handoff** target against an ASAP gateway, and bridges ASAP **`task_stream`** payloads into incremental **text deltas**.

## Peer dependencies

| Package | Range |
|---------|-------|
| `@asap-protocol/client` | `^2.3.0` |
| `@openai/agents` | `^0.11.0` (patch drift tolerated; CI pins **`0.11.4`** + **`latest`**) |
| `zod` | `^4.1.8` (aligned with `@openai/agents`; Zod 3 is not supported) |

Node **≥ 18**.

!!! warning "Pre-1.0 `@openai/agents` API drift"

    **`@openai/agents` is pre-1.0.** Minor/patch releases may change `Agent` constructor options, `tool()` parameter shapes, or default tool error handling. Pin your dependency and re-run tests after upgrades; CI exercises **`latest`** and **`0.11.4`**.

## Usage

See [`docs/integrations/openai-agents.md`](../../../docs/integrations/openai-agents.md) and [`apps/example-openai-agents/`](../../../apps/example-openai-agents/).

```typescript
import { Agent, run, setDefaultOpenAIKey } from "@openai/agents";
import type { AsapExecuteClient } from "@asap-protocol/client";
import { asapToolsForOpenAIAgents } from "@asap-protocol/openai-agents";

declare const client: AsapExecuteClient;

setDefaultOpenAIKey(process.env.OPENAI_API_KEY ?? "");

const tools = await asapToolsForOpenAIAgents(client);
const agent = new Agent({
  name: "demo",
  instructions: "Use ASAP tools when helpful.",
  model: "gpt-4o-mini",
  tools: [...tools],
});

await run(agent, "Echo hello via ASAP.");
```

## Status

Pre-release **`0.0.0`** workspace package until publish workflow promotes a semver tag.
