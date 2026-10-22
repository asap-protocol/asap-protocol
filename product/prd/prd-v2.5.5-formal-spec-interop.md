# PRD: ASAP Protocol v2.5.5 — Formal Spec & Interop

> **Product Requirements Document**
>
> **Version**: 2.5.5
> **Status**: PLANNED — **next** product train (soft inputs shipped 2026-07-18). **Filename is historical** — it is **not** the PyPI library tag; shipped library line is **2.5.8** as of 2026-10-21.
> **Created**: 2026-03-20 (origin in `prd-v2.4-adoption.md`); **rescoped**: 2026-06-22 → v2.5.3; **2026-07-08 → v2.5.5**
> **Last Updated**: 2026-10-21
> **Parent train**: [prd-v2.5-roadmap.md](./prd-v2.5-roadmap.md)
> **Predecessor**: [prd-v2.5.4-distribution-loop.md](./prd-v2.5.4-distribution-loop.md) (✅ shipped — soft narrative/examples)
> **Successor**: [prd-v3.0-economy.md](./prd-v3.0-economy.md)

---

## 1. Purpose

v2.5.5 closes the **standards-track loop** deferred since v2.3: formal RFC-style specification, capability-aware introspection, privacy considerations, and thin cross-protocol compatibility — **after** MCP Auth Bridge (v2.5.0) stabilizes the MCP integration surface and adoption work (v2.5.3–v2.5.4) supplies narrative examples.

---

## 2. Goals

| Goal | Metric | Priority |
|------|--------|----------|
| Formal specification | Published at `docs.asap-protocol.com/specification` | P1 |
| Capability-Aware Introspection | RFC 7662-style endpoint operational | P2 |
| Privacy spec | Formal Privacy Considerations section | P2 |
| Cross-protocol interop | ≥1 adapter + interoperability guide | P3 |

---

## 3. Functional requirements

### 3.1 Formal ASAP Specification (P1)

| ID | Requirement | Priority |
|----|-------------|----------|
| SPEC-001 | Complete spec: identity, registration, capabilities, approval, lifecycle, transport, discovery | MUST |
| SPEC-002 | RFC 2119 conformance keywords | MUST |
| SPEC-003 | Data models: Host, Agent, Capability Grant, Envelope | MUST |
| SPEC-004 | Authentication flows (Host JWT, Agent JWT, algorithms) | MUST |
| SPEC-005 | Error format + error code registry | MUST |
| SPEC-006 | Security considerations | MUST |
| SPEC-007 | Privacy considerations (see §3.3) | MUST |
| SPEC-008 | Published at `docs.asap-protocol.com/specification` | SHOULD |
| SPEC-009 | MCP Auth Bridge section referencing v2.5.0 implementation | SHOULD |

### 3.2 Capability-Aware Introspection (P2)

| ID | Requirement | Priority |
|----|-------------|----------|
| INTRO-001 | `POST /asap/agent/introspect` — agent JWT → active/inactive + compact grants | MUST |
| INTRO-002 | Response: `agent_id`, `host_id`, `user_id`, `agent_capability_grants`, `mode` | MUST |
| INTRO-003 | Compact grants only (capability + status) | MUST |
| INTRO-004 | Endpoint protected (shared secret, mTLS, or IP restriction) | SHOULD |

### 3.3 Privacy Considerations (P2)

| ID | Requirement | Priority |
|----|-------------|----------|
| PRIV-001 | Host key correlation risk documentation | SHOULD |
| PRIV-002 | Data retention guidance for agent activity logs | SHOULD |
| PRIV-003 | Capability requests as behavioral signals | SHOULD |
| PRIV-004 | `reason` field PII sensitivity | SHOULD |

### 3.4 Cross-Protocol Compatibility (P3)

| ID | Requirement | Priority |
|----|-------------|----------|
| COMPAT-001 | Accept external agent JWTs → ASAP agent sessions | SHOULD |
| COMPAT-002 | Translate external capability grants → ASAP grants | SHOULD |
| COMPAT-003 | Discovery bridge: ASAP well-known alongside other protocol endpoints | SHOULD |
| COMPAT-004 | Interoperability guide for multi-protocol environments | MUST |
| COMPAT-005 | A2A Agent Card → ASAP Manifest mapping (runtime bridge or documented gateway) | SHOULD |

### 3.5 TypeScript OpenAPI Adapter (P3 — conditional)

Ship only if demand at Spec kickoff **and** `@asap-protocol/openapi` still does not exist. **Default: defer** (Python OpenAPI adapter + Dist OpenAPI starter already cover the path).

| ID | Requirement | Priority |
|----|-------------|----------|
| TSOA-001 | TypeScript port of `asap.adapters.openapi` as `@asap-protocol/openapi` | SHOULD |
| TSOA-002 | API parity with Python `createFromOpenAPI(spec)` | SHOULD |

---

## 4. Non-goals

Same as v2.5 train — economy, federated registry, gRPC. MCP Auth Bridge is **not** in this PRD (shipped in v2.5.0). Security follow-ups from v2.4.1 §8 shipped in **v2.5.2**.

Also **not** this PRD: Distribution Loop homepage rewrite, starter pack, public metrics UI, `create-asap` CLI, `@asap-protocol/mcp-auth` npm (see Dist OOS + v2.5.0 backlog). Do not absorb Dist OOS as Spec MUST.

---

## 5. Prerequisites

| Prerequisite | Kind | Source |
|--------------|------|--------|
| v2.5.0 MCP Auth Bridge shipped | **Hard** (SPEC-009) | Stable MCP adapter API |
| Identity/capability model stable | **Hard** | v2.2+ |
| v2.5.4 Distribution Loop (or equivalent narrative + starters) | **Soft** (shipped) | [tag `v2.5.4`](https://github.com/adriannoes/asap-protocol/releases/tag/v2.5.4) — [PRD §11 handoff](./prd-v2.5.4-distribution-loop.md#11-handoff-inputs-for-v255-formal-spec) |
| v2.5.3 Adapter Lab II guides | **Soft** | Optional workflow / NAT / MAF narrative |

**Kickoff rule:** Dist Loop **shipped** (2026-07-18). Spec kickoff may cite the Dist artifacts below as soft narrative examples.

**Expected Dist inputs (shipped):**

| Artifact | Path |
|----------|------|
| Guide | [`docs/guides/build-for-agents.md`](../../docs/guides/build-for-agents.md) |
| Starters | `examples/starters/{openapi-provider,typescript-consumer,mcp-auth-bridge}/` |
| Narrative | Dist PRD §6 / D1 · homepage CTAs on `asap-protocol.com` |

---

## 6. Related documents

- **Distribution Loop (predecessor, soft):** [prd-v2.5.4-distribution-loop.md](./prd-v2.5.4-distribution-loop.md)
- **MCP Auth Bridge**: [prd-v2.5.0-mcp-auth-bridge.md](./prd-v2.5.0-mcp-auth-bridge.md)
- **Security follow-up**: [prd-v2.5.2-security-follow-up.md](./prd-v2.5.2-security-follow-up.md)
- **Train index**: [prd-v2.5-roadmap.md](./prd-v2.5-roadmap.md)
- **Economy (successor, trigger-gated):** [prd-v3.0-economy.md](./prd-v3.0-economy.md)
- **Legacy**: [prd-v2.4-adoption.md](./prd-v2.4-adoption.md)

---

## Change Log

| Date | Change |
|------|--------|
| 2026-07-18 | Dist Loop **shipped** (tag `v2.5.4`); soft Dist inputs marked available for Spec kickoff |
| 2026-07-18 | Hard vs soft prerequisites; Dist handoff paths; TSOA default defer; non-goals vs Dist OOS |
| 2026-07-08 | Renumbered v2.5.3 → **v2.5.5** (train shift after v2.5.2 security ship) |
| 2026-06-22 | Split from `prd-v2.4-adoption.md` §4.2–4.6; renumbered to v2.5.3 |
