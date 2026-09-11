# PRD: ASAP Protocol v2.5.x — Interop & Adoption Train

> **Product Requirements Document (train index)**
>
> **Version**: 2.5.x
> **Status**: ACTIVE
> **Created**: 2026-06-22
> **Last Updated**: 2026-09-11
>
> **Predecessor**: [prd-v2.4.1-security-hardening.md](./prd-v2.4.1-security-hardening.md) (✅ shipped 2026-06-14)
> **Successor (long-term)**: [prd-v3.0-economy.md](./prd-v3.0-economy.md)

---

## 1. Why v2.5.x exists

Between **v2.4.1** (security patch) and **v3.0** (economy), the project needs a coherent minor train that:

1. **Closes the MCP auth gap** — ASAP identity on MCP tool execution (convergência A2A+MCP alinhada à literatura recente sobre taxonomias de protocolos de agentes).
2. **Hardens the core** — quality patch (v2.5.1) + security/correctness follow-up (v2.5.2).
3. **Continues adoption** — framework/workflow adapters e distribution loop (antes planejados como v2.3.2/v2.3.3).
4. **Finalizes standards-track artifacts** — spec formal, introspection, privacy (antes em `prd-v2.4-adoption.md`).

### Versioning history (rescope log)

| Old label | New label | Reason |
|-----------|-----------|--------|
| `prd-v2.4-adoption.md` (Spec & Interop) | **v2.5.0–v2.5.5** | Número **2.4.0** foi usado para Edge-AI Discovery |
| `prd-v2.3.2` (Adapter Lab II) | **v2.5.1** → slipped → **v2.5.3** | v2.5.1 = quality patch; v2.5.2 = security follow-up |
| `prd-v2.3.3` (Distribution Loop) | **v2.5.2** → **v2.5.4** | v2.5.2 reassigned to security patch |
| Spec formal + introspection + privacy | **v2.5.3** → **v2.5.5** | Standards track após adoption loop |
| v2.4.1 §8 security follow-ups | **v2.5.4** (optional) → **v2.5.2** | Absorbed into next ship after v2.5.1 (2026-07-08) |
| Code quality patch | **v2.5.1** | Behavior-preserving refactor + P0 fixes (2026-06-26) |
| Library security/quality patch | **v2.5.5** (2026-09-11) | Reused the 2.5.5 number; Formal Spec PRD filename stays `prd-v2.5.5-formal-spec-interop.md` |

---

## 2. Train schedule

| Version | Codename | Primary outcome | PRD | Status |
|---------|----------|-----------------|-----|--------|
| **v2.5.0** | MCP Auth Bridge | ASAP Agent JWT + capabilities em MCP `tools/call` | [prd-v2.5.0-mcp-auth-bridge.md](./prd-v2.5.0-mcp-auth-bridge.md) | **✅ Shipped** 2026-06-24 — tag [`v2.5.0`](https://github.com/adriannoes/asap-protocol/releases/tag/v2.5.0) |
| **v2.5.1** | Code quality patch | Code-quality audit S0–S3 + P0 correctness/security fixes | *(execution: `engineering/tasks/private/v2.5.1/`)* | **✅ Shipped** 2026-06-26 — tag [`v2.5.1`](https://github.com/adriannoes/asap-protocol/releases/tag/v2.5.1) |
| **v2.5.2** | Security & correctness follow-up | #209 (operator auth, `extra="forbid"`, Redis JTI, web rate limits) + CR #245–#249 + registry fixes | [prd-v2.5.2-security-follow-up.md](./prd-v2.5.2-security-follow-up.md) | **✅ Shipped** 2026-07-08 — tag [`v2.5.2`](https://github.com/adriannoes/asap-protocol/releases/tag/v2.5.2) |
| **v2.5.3** | Adapter Lab II | Enterprise/workflow adapters (ex v2.3.2) | [prd-v2.5.3-adapter-lab-ii.md](./prd-v2.5.3-adapter-lab-ii.md) | **✅ Shipped** 2026-07-16 — tag [`v2.5.3`](https://github.com/adriannoes/asap-protocol/releases/tag/v2.5.3) · [tasks](../../engineering/tasks/v2.5.3/tasks-v2.5.3-roadmap.md) |
| **v2.5.4** | Distribution Loop | Homepage, starters, métricas (ex v2.3.3) | [prd-v2.5.4-distribution-loop.md](./prd-v2.5.4-distribution-loop.md) · [tasks](../../engineering/tasks/v2.5.4/tasks-v2.5.4-roadmap.md) | **✅ Shipped** 2026-07-18 — tag [`v2.5.4`](https://github.com/adriannoes/asap-protocol/releases/tag/v2.5.4) |
| **v2.5.5** | Security & quality patch | OpenAPI climb, registry URN guard, marketplace SSRF pin, capability-replace consent, fail-closed reactivate | `CHANGELOG.md` `[2.5.5]` (no Formal Spec PRD) | **✅ Shipped** 2026-09-11 — tag [`v2.5.5`](https://github.com/asap-protocol/asap-protocol/releases/tag/v2.5.5) |
| Formal Spec & Interop | Formal Spec & Interop | RFC spec, introspection, privacy, cross-protocol | [prd-v2.5.5-formal-spec-interop.md](./prd-v2.5.5-formal-spec-interop.md) | **Next** (post-2.5.5; filename historical — create `engineering/tasks/v2.5.5/` at kickoff) |

**Execution rule:** **v2.5.0–v2.5.5** shipped (library **2.5.5** is the security/quality patch). **Next:** Formal Spec ([prd-v2.5.5-formal-spec-interop.md](./prd-v2.5.5-formal-spec-interop.md); filename historical, not this library tag). **v3.0** remains trigger-gated ([prd-v3.0-economy.md](./prd-v3.0-economy.md)).

**Patch tags (not minor releases):** [`v2.5.0.1`](https://github.com/adriannoes/asap-protocol/releases/tag/v2.5.0.1) republished **`asap-compliance` 1.3.0** only; `pyproject.toml` remained **2.5.0**. **`@asap-protocol/mcp-auth`** (npm) is still deferred — future npm patch TBD (do not confuse with tag `v2.5.0.1`).

### Parked / orphan owners (not Dist MUST)

| Item | Owner | Rule |
|------|-------|------|
| `@asap-protocol/mcp-auth` (npm) | [v2.5.0 backlog](../../engineering/tasks/v2.5.0/backlog-mcp-auth-typescript.md) | npm patch TBD — not v2.5.4 / not Spec MUST |
| `@asap-protocol/openapi` (TSOA) | [prd-v2.5.5](./prd-v2.5.5-formal-spec-interop.md) §3.5 | **Default defer** unless demand at Spec kickoff |
| Fourth starter (workflow) | Optional post–Dist | Lab II `examples/workflow_asap_connector/` already public |
| Registry API (PostgreSQL) | Trigger: 500 agents / IssueOps bottleneck | Deferred; not a v2.5.x or v3.0 silent prereq |
| Design System Revamp | Separate design track | Out of Dist |
| `create-asap` / scaffold CLI | Post–v2.5.4 if demanded | Out of Dist |
| Public metrics dashboard UI | — | Out of Dist (D4); maintainer telemetry only |
| G5/G6 governance product | After Formal Spec (local strategy) | Do not schedule as v2.5.x MUST |

---

## 3. Strategic positioning (pilha federada)

```
L4 Interaction     ASAP agent-to-agent (v2.2+)
L3 Execution       MCP tools ← v2.5.0 Auth Bridge fecha este gap
L2 Discovery       well-known + Lite Registry (v2.3+)
L1 Identity        Host/Agent JWT + capabilities (v2.2+)
```

Narrativa pública: **ASAP não substitui MCP** — fornece a camada de identidade e policy que MCP não padroniza.

---

## 4. Prerequisites (train-wide)

| Prerequisite | Status |
|-------------|--------|
| v2.4.1 security patch shipped | ✅ 2026-06-14 |
| Capability model stable (v2.2+) | ✅ |
| Agent JWT + Host JWT (v2.2+) | ✅ |
| MCP server/client in tree (`asap.mcp`) | ✅ stdio + tools |
| Envelope MCP payloads (`McpToolCall`) | ✅ A2A path; native MCP auth via v2.5.0 bridge |
| OpenAPI Adapter (v2.3.0) | ✅ |
| v2.5.1 quality patch | ✅ 2026-06-26 |
| v2.5.2 security follow-up shipped | ✅ 2026-07-08 |

---

## 5. Non-goals (entire v2.5 train)

| Feature | When |
|---------|------|
| Economy / billing | v3.0 (trigger-gated) |
| Federated registry | v3.x+ |
| gRPC binding | TBD |
| Schema negotiation runtime (Agora-style) | Out of scope |
| Registry API backend (PostgreSQL) | Deferred until 500-agent trigger |

**Handoff chain:** Dist Loop §11 → Formal Spec soft inputs → Economy §8 proxies. See [prd-v2.5.4 §11](./prd-v2.5.4-distribution-loop.md#11-handoff-inputs-for-v255-formal-spec).

---

## 6. Related documents

- **Shipped v2.4.0**: [prd-v2.4.0-edge-ai-discovery.md](./prd-v2.4.0-edge-ai-discovery.md)
- **Shipped v2.4.1**: [prd-v2.4.1-security-hardening.md](./prd-v2.4.1-security-hardening.md)
- **Adoption foundation**: [prd-v2.3-scale.md](./prd-v2.3-scale.md)
- **Legacy redirect**: [prd-v2.4-adoption.md](./prd-v2.4-adoption.md)
- **Tasks**: [engineering/tasks/README.md](../../engineering/tasks/README.md)
- **Economy (long-term)**: [prd-v3.0-economy.md](./prd-v3.0-economy.md)

---

## Change Log

| Date | Change |
|------|--------|
| 2026-09-11 | **Library 2.5.5 shipped** — security/quality patch (tag `v2.5.5`); Formal Spec stays **next** under historical filename `prd-v2.5.5-formal-spec-interop.md` |
| 2026-07-18 | **v2.5.4 shipped** — tag `v2.5.4`; PyPI 2.5.4; next → Formal Spec **v2.5.5** |
| 2026-07-18 | v2.5.4 status → **Active** (tasks ACTIVE; S0–S2 producer done) |
| 2026-07-18 | Parked/orphan owners table; Dist→Spec→Economy handoff pointer; v2.5.5 soft-block clarified |
| 2026-07-18 | v2.5.4 tasks pack created — [tasks-v2.5.4-roadmap.md](../../engineering/tasks/v2.5.4/tasks-v2.5.4-roadmap.md); PRD D1–D5 locked |
| 2026-07-08 | Rescope: v2.5.2 = security follow-up; Adapter Lab II → v2.5.3; Distribution Loop → v2.5.4; Formal Spec → v2.5.5; document v2.5.1 as quality patch |
| 2026-07-08 | **v2.5.2 shipped** — tag `v2.5.2`; PyPI 2.5.2; umbrella #209 closed |
| 2026-07-11 | v2.5.3 status → Ready for kickoff; linked [tasks-v2.5.3-roadmap.md](../../engineering/tasks/v2.5.3/tasks-v2.5.3-roadmap.md) |
| 2026-07-16 | **v2.5.3 shipped** — tag `v2.5.3`; PyPI 2.5.3; v2.5.4 → Ready for kickoff |
| 2026-06-22 | Creation; tasks 1.0–5.0 added to sprint index |
| 2026-06-24 | Sprint sub-tasks S0–S5 finalized; v2.5.0 marked ready for implementation |
| 2026-06-24 | v2.5.0 S0–S4 merged on `release/2.5.0`; TS `@asap-protocol/mcp-auth` deferred |
