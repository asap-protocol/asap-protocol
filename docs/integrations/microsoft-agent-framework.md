# Microsoft Agent Framework ↔ ASAP (interop)

> **Support tier:** Experimental (Tier 3) — no compatibility promise. See [support tiers](./support-tiers.md).

How [Microsoft Agent Framework (MAF)](https://learn.microsoft.com/en-us/agent-framework/overview/) tool surfaces (in-process **`AIFunction`** / **`AITool`**, MCP) sit beside ASAP **Host/Agent JWT** and **capability grants**. Naming and packages below are accurate as of **2026-07-13** (MAF **1.0** GA).

!!! warning "Research / experimental — not maintained"

    This page is **interop guidance** from Adapter Lab II (v2.5.3). It is **not** a first-class ASAP adapter like [Mastra](./mastra.md) or [OpenAI Agents](./openai-agents.md): there is **no** ASAP .NET SDK, **no** NuGet package, and **no** in-repo C# sample under `examples/`. Treat recommendations as research notes — they may change without a deprecation cycle.

## Purpose

Use this when a .NET (or Python MAF) agent already speaks MAF tools / MCP and you need ASAP identity and authorization to remain the source of truth for remote capability execution.

This guide:

1. Maps ASAP **Agent JWT** + **capability grants** to how MAF registers tools (`AIFunctionFactory` / MCP `AITool`s).
2. States honest limits: conceptual interop only — you wire HTTP/JSON-RPC yourself (or via Python ASAP + MCP bridge).
3. Points Semantic Kernel readers at Microsoft’s migration path (SK is legacy here).

**Contrast with Lab I adapters:** Mastra and OpenAI Agents ship TypeScript packages that turn ASAP capabilities into framework tools. MAF has **no** equivalent ASAP package in this release.

## Requirements (your stack — not ASAP packages)

| Piece | Note |
|-------|------|
| MAF .NET | NuGet **`Microsoft.Agents.AI`** (and MEAI **`AIFunction`** / **`AITool`** types) |
| MAF Python | PyPI **`agent-framework`** |
| ASAP side | Python **`asap-protocol`** (identity, grants, transport) and/or TypeScript **`@asap-protocol/client`** — **not** a .NET SDK |
| Docs | [aka.ms/AgentFramework/Docs](https://aka.ms/AgentFramework/Docs) · [microsoft/agent-framework](https://github.com/microsoft/agent-framework) |

## Capability mapping

ASAP authorizes **who** may invoke **which** skill under **which** constraints. MAF decides **how** a model-visible tool is registered and invoked inside the agent runtime. Keep those layers distinct.

| ASAP concept | MAF-side analogue | Interop note |
|--------------|-------------------|--------------|
| Capability / skill id (URN or manifest skill) | Tool name on an **`AIFunction`** / MCP tool | Prefer stable ids that match the ASAP grant / manifest skill — do not invent a parallel naming scheme |
| Capability **input/output** JSON Schema | Function parameters / MCP tool schema | Discover via ASAP `describeCapability` (or manifest); mirror into `AIFunctionFactory.Create` or MCP tool metadata |
| **Agent JWT** (caller identity) | Not a MAF primitive | Attach on outbound ASAP HTTP (Bearer / client config) or, for MCP stdio tools, `_meta.asap_agent_jwt` when using [MCP Auth Bridge](../adapters/mcp-auth-bridge.md) |
| **Capability grant** + constraints | Tool allowlist on the agent | Register only tools that correspond to **granted** capabilities; enforce again on the ASAP host (never trust MAF alone) |
| Approval / escalation | MAF human-in-the-loop (optional) | ASAP [capability escalation](../capabilities/escalation.md) remains the protocol path for expanding grants |
| Host identity / registration | Out of band | See [Security](../security.md) and [Migration — per-runtime agent identity](../migration.md#upgrading-from-v21x-to-v220) |

### Pattern A — In-process `AIFunction` wrapping ASAP execute

Register one MAF tool per granted ASAP capability. The tool body calls ASAP (`task.request` / capability execute) with the Agent JWT and capability URN.

Conceptual shape (.NET naming as of MAF 1.0):

```csharp
// Pseudocode — no ASAP .NET SDK; use HttpClient / your JSON-RPC client.
AIFunction asapEcho = AIFunctionFactory.Create(
    async (string message, CancellationToken ct) =>
    {
        // POST ASAP provider: execute granted capability with Agent JWT.
        // capability id must match the grant (e.g. urn:...:echo).
        return await ExecuteAsapCapabilityAsync("urn:asap:cap:echo", new { message }, ct);
    });

AIAgent agent = chatClient.AsAIAgent(
    instructions: "Use ASAP tools only when they match the user task.",
    tools: [asapEcho]);
```

MAF docs: [Function tools](https://learn.microsoft.com/en-us/agent-framework/agents/tools/function-tools).

### Pattern B — MCP tools as `AITool`s (ASAP behind MCP)

MAF can list MCP server tools and pass them to the agent as **`AITool`** instances (MCP C# SDK → cast to `AITool`). If the MCP server is an ASAP-backed stdio process protected with **`protect_server`**, each protected `tools/call` must carry **`_meta.asap_agent_jwt`**; the bridge verifies JWT and grants.

```text
MAF agent  --MCP tools/call-->  MCP server (asap.mcp.auth.protect_server)
                                      |
                                      +-- verify Agent JWT + capability grant
                                      +-- invoke underlying tool / ASAP skill
```

- MAF MCP client: [Using MCP tools](https://learn.microsoft.com/en-us/agent-framework/agents/tools/local-mcp-tools)
- ASAP enforcement: [MCP Auth Bridge](../adapters/mcp-auth-bridge.md), [MCP integration](../mcp-integration.md)

Do **not** treat MCP tool discovery alone as authorization — grants still live on the ASAP host.

## What ASAP still owns

Regardless of MAF registration style:

1. **Identity** — Host/Agent JWT lifecycle (register, revoke, rotate). See [Security](../security.md).
2. **Authorization** — Capability grants, constraints, approval/escalation. See [Capabilities](../capabilities/index.md).
3. **Wire contract** — Envelope + JSON-RPC over HTTP/WebSocket; no protocol fork for MAF.
4. **MCP Mode A** — Opt-in JWT + grant checks on stdio `tools/call` via the Auth Bridge.

MAF owns session memory, model routing, and local tool orchestration. ASAP owns remote trust.

## Semantic Kernel (legacy / migration)

**Semantic Kernel (SK)** is on a **maintenance** track. Prefer **MAF** (`Microsoft.Agents.AI` / `agent-framework`) for new work. If you still run SK plugins/kernel functions, treat them like Pattern A: thin wrappers that call ASAP with a valid Agent JWT — then plan migration to MAF tools.

Microsoft migration: [From Semantic Kernel](https://learn.microsoft.com/en-us/agent-framework/migration-guide/from-semantic-kernel/). Background: [SK and Microsoft Agent Framework](https://devblogs.microsoft.com/agent-framework/semantic-kernel-and-microsoft-agent-framework/).

## Limits and non-goals

| Claim | Reality in v2.5.3 |
|-------|-------------------|
| First-class MAF adapter package | **No** — guide only |
| ASAP .NET / NuGet SDK | **Does not exist** |
| In-repo C# sample / `setup-dotnet` CI | **No** (see S1b 2b.3 — deferred) |
| Protocol changes for MAF | **Out of scope** |
| Parity with Mastra / OpenAI Agents | **Not claimed** |

If you need a maintained TypeScript path today, use [Mastra](./mastra.md) or [OpenAI Agents](./openai-agents.md). For HTTP workflow hosts → ASAP skills, see [Workflow connectors](./workflow-connectors.md).

## Related

- Spike lock: [`engineering/tasks/v2.5.3/research-semantic-kernel.md`](https://github.com/asap-protocol/asap-protocol/blob/main/engineering/tasks/v2.5.3/research-semantic-kernel.md)
- [Security](../security.md) — Agent JWT / Host identity
- [Capabilities](../capabilities/index.md) · [Escalation](../capabilities/escalation.md)
- [MCP Auth Bridge](../adapters/mcp-auth-bridge.md) · [MCP integration](../mcp-integration.md)
- [Mastra](./mastra.md) · [OpenAI Agents](./openai-agents.md) — maintained Lab I adapters
- MAF overview: [learn.microsoft.com/agent-framework](https://learn.microsoft.com/en-us/agent-framework/overview/)
