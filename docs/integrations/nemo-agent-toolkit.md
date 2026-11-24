# NeMo Agent Toolkit ↔ ASAP (interop)

> **Support tier:** Experimental (Tier 3) — no compatibility promise. See [support tiers](./support-tiers.md).

How [NVIDIA NeMo Agent Toolkit (NAT)](https://docs.nvidia.com/nemo/agent-toolkit/latest/) workflows sit beside ASAP **Host/Agent JWT** and **capability grants**. Naming and pins below are accurate as of **2026-07-13** (`nvidia-nat` **1.8.0**).

!!! warning "Experimental — Path A demo exists; not a maintained adapter"

    This page is **interop guidance** from Adapter Lab II (v2.5.3). A reproducible **Path A** stdio demo lives under [`examples/nemo_agent_toolkit_asap/`](https://github.com/asap-protocol/asap-protocol/tree/main/examples/nemo_agent_toolkit_asap). It remains **experimental** until a maintained promotion decision — there is **no** first-class ASAP↔NAT adapter package, **no** published `nemo-agent-toolkit-asap` plugin, and **no** claim of native NAT↔ASAP protocol support. Treat recommendations as research notes — they may change without a deprecation cycle.

## Purpose

Use this when a NAT workflow already speaks **MCP** and/or **A2A** and you need ASAP identity and authorization to remain the source of truth for remote capability execution.

This guide:

1. Positions ASAP as an **identity / capability complement** — not an A2A replacement inside NAT.
2. Documents Path A (NAT `mcp_client` **stdio** → ASAP `protect_server`) and honest transport limits.
3. Contrasts NAT OAuth2/Keycloak user JWTs with ASAP Host/Agent JWT + grants (no equivalence).
4. Points to the A2A v1.0 Agent Card ↔ manifest mapping that `asap.adapters.a2a` ships in v2.6.0.

**Contrast with Lab I adapters:** Mastra and OpenAI Agents ship TypeScript packages that turn ASAP capabilities into framework tools. NAT already has first-class MCP/A2A plugins; ASAP wires **policy** onto tools those plugins call — it does not replace NAT’s conductor.

## What NAT is

NeMo Agent Toolkit (formerly AgentIQ / AIQ) is a **framework-agnostic conductor**: YAML workflows, `nat` CLI, profiling/eval, plus **native MCP and A2A** plugins. It can wrap LangChain, LlamaIndex, CrewAI, Semantic Kernel, and others.

| Layer | NAT today | ASAP today |
|-------|-----------|------------|
| Tool protocol | MCP client + MCP/FastMCP server | MCP Auth Bridge (`protect_server`) + envelope MCP payloads |
| Agent protocol | A2A client + `nat a2a serve` | ASAP envelope / task FSM / well-known manifest |
| Auth on protected examples | OAuth2 + Keycloak (user JWT, scopes, JWKS) | Host/Agent JWT + `CapabilityRegistry` grants |
| Discovery | A2A Agent Card `/.well-known/agent-card.json` | ASAP manifest / Lite Registry / well-known |

Upstream docs: [docs.nvidia.com/nemo/agent-toolkit](https://docs.nvidia.com/nemo/agent-toolkit/latest/). Spike map: [`research-nemo-agent-toolkit.md`](https://github.com/asap-protocol/asap-protocol/blob/main/engineering/tasks/v2.5.3/research-nemo-agent-toolkit.md).

## A2A vs MCP vs ASAP

| Protocol | Role in a NAT deployment | Role of ASAP |
|----------|--------------------------|--------------|
| **A2A** | Agent-to-agent runtime NAT already speaks (`a2a-sdk`) | **Complement, not replacement** — ASAP does not displace NAT’s A2A stack in v2.5.3 |
| **MCP** | Tool calling between NAT workflows and MCP servers | ASAP can **gate** MCP tools via Auth Bridge (Path A stdio) |
| **ASAP** | Optional remote trust layer for identity, grants, discovery, metering | Owns who may invoke which skill under which constraints |

Do **not** present ASAP as “A2A but better.” When NAT already uses A2A between agents, keep that path; use ASAP where you need Host/Agent identity, capability grants, compliance, or ASAP-native discovery.

## Agent Card and manifest

`asap.adapters.a2a` (v2.6.0) maps an A2A v1.0 Agent Card to an ASAP manifest and back, verifies the signatures of a card that you fetch, and signs the cards that an ASAP agent serves. The field table, with every field that one format cannot carry and the reason, is in the [interoperability guide](../guides/interoperability.md#agent-card-and-manifest-fields). The guide also covers [signed cards](../guides/interoperability.md#signed-agent-cards).

A card that an A2A server outside ASAP serves has no ASAP extension. To map it, you give the agent URN and the ASAP endpoint yourself. The NAT side of this mapping is not tested.

## Auth contrast (no equivalence)

| Dimension | NAT protected MCP/A2A examples | ASAP MCP Auth Bridge |
|-----------|-------------------------------|----------------------|
| Actor | End **user** (OAuth2 Authorization Code / Keycloak) | **Agent** identity (Host JWT → Agent JWT) |
| Token | OIDC/OAuth2 access JWT (JWKS, `iss` / `aud` / scopes) | ASAP Agent JWT (`verify_agent_jwt`) |
| Carriage | HTTP `Authorization: Bearer` on **streamable-http** | MCP `_meta.asap_agent_jwt` per `tools/call` (stdio); optional **dev-only** `ASAP_AGENT_JWT` env |
| Authorization | Scope + audience on the resource server | `CapabilityRegistry.check_grant` (+ constraints) |

These models are **complementary**, not interchangeable. Do not wire Keycloak as a stand-in for ASAP grants, and do not claim NAT `mcp_oauth2` talks to ASAP. Since v2.6.0, an ASAP server can give explicit grants to access tokens from an issuer that you list ([external access tokens](../guides/interoperability.md#external-access-tokens-on-an-asap-server-python)); that path is not tested with NAT.

!!! danger "Production hard-stop (env JWT fallback)"

    Path A’s `ASAP_AGENT_JWT` env fallback is **dev-only** and **unsafe for multi-tenant production**. **Do not deploy** the Path A example unchanged, and **do not** use env JWT fallback as a production auth carriage. Prefer in-band `_meta.asap_agent_jwt` (or a future HTTP Auth Bridge) for real deployments. Same stance as [MCP Auth Bridge](../adapters/mcp-auth-bridge.md) and the example README.

## Transport honesty (Path A)

| Transport | Path A for ASAP Auth Bridge? |
|-----------|------------------------------|
| **stdio** | **Yes** — NAT `mcp_client` spawns ASAP `protect_server` child; demo uses server-side `ASAP_AGENT_JWT` env fallback because NAT does not pass `_meta.asap_agent_jwt` |
| **streamable-http** / **SSE** | **No** — the Python bridge covers stdio only. `@asap-protocol/mcp-auth` (v2.6.0) protects Streamable HTTP MCP servers written in TypeScript, not NAT's Python MCP servers. HTTP+SSE is not supported |

See [MCP Auth Bridge: HTTP MCP servers in TypeScript](../adapters/mcp-auth-bridge.md#http-mcp-servers-in-typescript). Do not invent a Python HTTP protect path for NAT.

Full matrices: [research note §10](https://github.com/asap-protocol/asap-protocol/blob/main/engineering/tasks/v2.5.3/research-nemo-agent-toolkit.md#10-spike-gap-analysis-2026-07-13).

## Requirements

| Piece | Note |
|-------|------|
| Python | **3.13** for joint examples (ASAP ∩ `nvidia-nat` 1.8.x) |
| ASAP | `asap-protocol` in-repo (`uv sync`) — Auth Bridge via `asap.mcp.auth` |
| NAT (optional) | `nvidia-nat[mcp]==1.8.0` from `examples/nemo_agent_toolkit_asap/requirements.txt` — **not** a core ASAP dependency |
| NIM | `NVIDIA_API_KEY` only if you run `nat run` with the sample `react_agent` YAML |

## Quick start (commands)

### ASAP-side smoke (CI / no NAT / no NIM)

From the repository root:

```bash
uv run python examples/nemo_agent_toolkit_asap/smoke_asap_side.py
uv run python examples/nemo_agent_toolkit_asap/smoke_asap_side.py --stdio
uv run pytest tests/examples/test_nemo_agent_toolkit_asap.py -v
```

Or via the launcher:

```bash
./examples/nemo_agent_toolkit_asap/run_demo.sh smoke
./examples/nemo_agent_toolkit_asap/run_demo.sh smoke-stdio
```

These paths exercise `protect_server` + env JWT fallback **without** installing `nvidia-nat`. Main CI stays green when NAT is absent — ASAP-side tests always run; the optional NAT import uses `pytest.importorskip("nat")`.

### Optional full NAT workflow (maintainer / local)

```bash
uv pip install -r examples/nemo_agent_toolkit_asap/requirements.txt
export NVIDIA_API_KEY='your-key'   # never commit; required for sample react_agent
nat run --config_file examples/nemo_agent_toolkit_asap/configs/config-mcp-client-stdio.yml \
  --input "Call echo with message hello, then secure_action with action demo"
```

Or: `./examples/nemo_agent_toolkit_asap/run_demo.sh nat` (exits with a clear skip if `nat` is missing — does not fail ASAP CI).

Example README (same commands): [`examples/nemo_agent_toolkit_asap/README.md`](https://github.com/asap-protocol/asap-protocol/blob/main/examples/nemo_agent_toolkit_asap/README.md).

## Adjacent edge story (ShellClaw / CUDA)

NAT’s NIM / CUDA LLM path is **out of band** for this interop guide. For edge hardware advertising and static registry listing, see the separate ShellClaw story: [ShellClaw registry guide](../guides/shellclaw-registry.md) and [registry-shellclaw example](../examples/registry-shellclaw.md). That does not imply NAT↔ASAP native support on device.

## Limits and non-goals

| Claim | Reality in v2.5.3 |
|-------|-------------------|
| Maintained ASAP↔NAT adapter package | **No** — experimental Path A demo + this guide |
| Native NAT↔ASAP protocol / A2A replacement | **Not claimed** |
| HTTP / streamable-http Auth Bridge for NAT's Python MCP servers | **No** — `@asap-protocol/mcp-auth` covers TypeScript servers only |
| Published `nemo-agent-toolkit-asap` / `nat.plugins.asap` | **Out of ship** (Path C feasibility only — appendix below) |
| Protocol changes for NAT | **Out of scope** |

## Related

- Spike map: [`engineering/tasks/v2.5.3/research-nemo-agent-toolkit.md`](https://github.com/asap-protocol/asap-protocol/blob/main/engineering/tasks/v2.5.3/research-nemo-agent-toolkit.md)
- Runnable Path A: [`examples/nemo_agent_toolkit_asap/`](https://github.com/asap-protocol/asap-protocol/tree/main/examples/nemo_agent_toolkit_asap)
- [MCP Auth Bridge](../adapters/mcp-auth-bridge.md) (Mode A `protect_server`) · [MCP integration](../mcp-integration.md) (Mode B envelopes)
- [Automation connector security](../guides/automation-connector-security.md) — Mode A vs Mode B; env JWT fallback is **dev-only**
- [Security](../security.md) — Agent JWT / Host identity
- [Capabilities](../capabilities/index.md)
- [Workflow connectors](./workflow-connectors.md) · [Microsoft Agent Framework](./microsoft-agent-framework.md)
- Upstream: [NeMo Agent Toolkit docs](https://docs.nvidia.com/nemo/agent-toolkit/latest/) · [NVIDIA/NeMo-Agent-Toolkit](https://github.com/NVIDIA/NeMo-Agent-Toolkit)

---

## Appendix: Path C third-party plugin (feasibility — out of ship)

Upstream encourages provider-owned packages ([third-party plugins](https://docs.nvidia.com/nemo/agent-toolkit/latest/extend/third-party-plugins.html)):

| Surface | Convention (sketch) |
|---------|----------------------|
| Import | `nat.plugins.asap` |
| Dist name | `nemo-agent-toolkit-asap` |
| Repo name | `NeMo-Agent-Toolkit-asap` (or live under ASAP monorepo `packages/`) |
| Entry | `nat_asap` / function group e.g. `asap__discover`, `asap__task_request` |

**Ownership options:** (1) ASAP-maintained package in this monorepo; (2) separate NVIDIA-style provider repo. Either needs a clear owner before publish.

**CI matrix sketch (post–v2.5.3):** Python **3.11–3.13** × pinned `nvidia-nat==1.8.0` (joint Path A examples stay on **3.13** only). Optional extra — must not become a required ASAP core dep.

**Explicit out of ship for v2.5.3:** do **not** publish `nemo-agent-toolkit-asap`, register `nat.plugins.asap`, or claim a Public Plugin API integration in this release. Path C remains a feasibility note until Path A is promoted and maintainership is decided. Checklist: [research note §6](https://github.com/asap-protocol/asap-protocol/blob/main/engineering/tasks/v2.5.3/research-nemo-agent-toolkit.md#6-third-party-plugin-feasibility-checklist-spike).
