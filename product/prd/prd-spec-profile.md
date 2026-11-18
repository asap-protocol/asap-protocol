# PRD: ASAP Protocol v2.7.0 — Spec Profile

> **Product Requirements Document**
>
> **Version**: 2.7.0 (target minor)
> **Status**: PLANNED — after Interop ([prd-interop.md](./prd-interop.md), v2.6.0)
> **Created**: 2026-03-20 (origin in `prd-v2.4-adoption.md`); **rescoped**: 2026-06-22 → v2.5.3; 2026-07-08 → v2.5.5; **2026-11-18 → v2.7.0 Spec profile** (renamed from `prd-v2.5.5-formal-spec-interop.md`; COMPAT-* split to Interop)
> **Last Updated**: 2026-11-18
> **Parent**: [prd-roadmap.md](./prd-roadmap.md)
> **Predecessor**: [prd-interop.md](./prd-interop.md) (v2.6.0)
> **Successor**: decision gate in [prd-roadmap.md §2](./prd-roadmap.md#2-roadmap). Economy is a separate product track ([prd-v3.0-economy.md](./prd-v3.0-economy.md)).
> **Tasks**: create `engineering/tasks/v2.7.0/` at kickoff

---

## 1. Purpose

v2.7.0 closes the standards-track loop deferred since v2.3. It publishes the ASAP specification as a **profile** layered on A2A v1.0, MCP authorization (OAuth 2.1), and OAuth.

The spec covers only what ASAP adds:

- per-runtime agent identity (Host/Agent JWT)
- constraint-based capability grants
- approval flows (device authorization, CIBA, WebAuthn, A2H)
- task lifecycle
- error format and error code registry

For everything else it cites A2A, MCP, and OAuth instead of restating them. Every MUST maps to an `asap-compliance` check, so conformance is something you can run, not only read.

**Why after Interop:** the spec describes how ASAP layers on A2A and MCP. Interop (v2.6.0) builds those layers first, so the spec documents shipped behavior instead of a design that may change.

---

## 2. Goals

| Goal | Metric | Priority |
|------|--------|----------|
| Spec profile | Published at `docs.asap-protocol.com/specification`; scoped to ASAP's unique surface; cites A2A / MCP / OAuth normatively | P1 |
| Testable conformance | 100% of MUST statements map to an `asap-compliance` check | P1 |
| Capability-aware introspection | RFC 7662-style endpoint operational | P2 |
| Privacy considerations | Formal Privacy Considerations section | P2 |

---

## 3. Functional requirements

### 3.1 Spec profile (P1)

| ID | Requirement | Priority |
|----|-------------|----------|
| SPEC-001 | Spec covers ASAP's unique surface: identity, registration, capability grants, approval, lifecycle. Transport and discovery sections profile A2A / MCP / OAuth and cite them instead of restating them. | MUST |
| SPEC-002 | RFC 2119 conformance keywords | MUST |
| SPEC-003 | Data models: Host, Agent, Capability Grant, Envelope | MUST |
| SPEC-004 | Authentication flows (Host JWT, Agent JWT, algorithms) | MUST |
| SPEC-005 | Error format + error code registry (includes the `asap:*` MCP codes) | MUST |
| SPEC-006 | Security considerations | MUST |
| SPEC-007 | Privacy considerations (see §3.3) | MUST |
| SPEC-008 | Published at `docs.asap-protocol.com/specification` | SHOULD |
| SPEC-009 | MCP section: Auth Bridge for stdio (v2.5.0) and HTTP via `@asap-protocol/mcp-auth` (v2.6.0) | SHOULD |
| SPEC-010 | Every MUST statement maps to at least one `asap-compliance` check. The spec ships a traceability table (statement → check). A MUST without a check blocks release. | MUST |
| SPEC-011 | Profile sections for A2A v1.0 and MCP authorization (OAuth 2.1): how ASAP identity, grants, and approval layer on Agent Cards and external tokens, based on the shipped Interop bridges ([prd-interop.md §12](./prd-interop.md#12-handoff-inputs-for-v270-spec-profile)) | MUST |

IETF agent identity drafts (WIMSE/AIMS, OAuth agent drafts) are cited as **informative** references only. No MUST depends on draft text.

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

### 3.4 Cross-Protocol Compatibility

Moved to [prd-interop.md](./prd-interop.md) (v2.6.0) on 2026-11-18. COMPAT-001..005 keep their IDs there.

### 3.5 TypeScript OpenAPI Adapter (P3 — conditional)

Ship only if there is demand at Spec kickoff **and** `@asap-protocol/openapi` still does not exist. **Default: defer** (the Python OpenAPI adapter and the Dist OpenAPI starter already cover the path).

| ID | Requirement | Priority |
|----|-------------|----------|
| TSOA-001 | TypeScript port of `asap.adapters.openapi` as `@asap-protocol/openapi` | SHOULD |
| TSOA-002 | API parity with Python `createFromOpenAPI(spec)` | SHOULD |

---

## 4. Non-goals

| Item | Where / note |
|------|--------------|
| Restating A2A, MCP, or OAuth | The profile cites them (SPEC-001) |
| Cross-protocol bridges, external tokens, `@asap-protocol/mcp-auth` | [prd-interop.md](./prd-interop.md) (v2.6.0) |
| MCP Auth Bridge implementation | Shipped in v2.5.0 (stdio); HTTP in v2.6.0 |
| Economy, federated registry, gRPC | [prd-roadmap.md §6](./prd-roadmap.md#6-non-goals-all-tracks-unless-a-track-prd-says-otherwise) |
| Distribution Loop leftovers: `create-asap` CLI, public metrics UI, Design System Revamp, fourth starter | [prd-roadmap.md §5](./prd-roadmap.md#5-parked-and-deferred-items) — do not absorb them as Spec MUST |
| Normative dependence on IETF drafts | Informative references only (§3.1) |

Security follow-ups from v2.4.1 §8 shipped in **v2.5.2**.

---

## 5. Prerequisites

| Prerequisite | Kind | Source |
|--------------|------|--------|
| Interop shipped (v2.6.0) | **Hard** for spec sections that describe bridges (SPEC-009 HTTP, SPEC-011) | [prd-interop.md](./prd-interop.md) |
| v2.5.0 MCP Auth Bridge shipped | **Hard** (SPEC-009) | Stable MCP adapter API |
| Identity / capability model stable | **Hard** | v2.2+ |
| `asap-compliance` can host new checks | **Hard** (SPEC-010) | `asap-compliance` 1.3.0+ (Interop adds a minor bump) |
| v2.5.4 Distribution Loop (narrative + starters) | **Soft** (shipped) | [tag `v2.5.4`](https://github.com/asap-protocol/asap-protocol/releases/tag/v2.5.4) — [PRD §11 handoff](./prd-v2.5.4-distribution-loop.md#11-handoff-inputs-for-v255-formal-spec) |
| v2.5.3 Adapter Lab II guides | **Soft** | Optional workflow / NAT / MAF narrative |

**Kickoff rule:** sections that do not describe bridges (identity, grants, approval, lifecycle, errors, privacy, introspection) may be drafted before v2.6.0 ships. SPEC-011 and the HTTP part of SPEC-009 wait for the shipped Interop bridges.

**Soft inputs available now:**

| Artifact | Path |
|----------|------|
| Guide | [`docs/guides/build-for-agents.md`](../../docs/guides/build-for-agents.md) |
| Starters | `examples/starters/{openapi-provider,typescript-consumer,mcp-auth-bridge}/` |
| Narrative | Dist PRD §6 / D1 · homepage CTAs on `asap-protocol.com` |
| Interop handoff (after v2.6.0) | [prd-interop.md §12](./prd-interop.md#12-handoff-inputs-for-v270-spec-profile) |

---

## 6. Exit criteria

v2.7.0 ships when the **spec is published and compliance covers 100% of MUST statements** (SPEC-010 traceability table shows no gaps).

---

## 7. Related documents

- **Train index**: [prd-roadmap.md](./prd-roadmap.md)
- **Interop (predecessor, v2.6.0)**: [prd-interop.md](./prd-interop.md)
- **Distribution Loop (soft input)**: [prd-v2.5.4-distribution-loop.md](./prd-v2.5.4-distribution-loop.md)
- **MCP Auth Bridge**: [prd-v2.5.0-mcp-auth-bridge.md](./prd-v2.5.0-mcp-auth-bridge.md)
- **Security follow-up**: [prd-v2.5.2-security-follow-up.md](./prd-v2.5.2-security-follow-up.md)
- **Closed v2.5.x train**: [prd-v2.5-roadmap.md](./prd-v2.5-roadmap.md)
- **Economy (product track, trigger-gated)**: [prd-v3.0-economy.md](./prd-v3.0-economy.md)
- **Legacy**: [prd-v2.4-adoption.md](./prd-v2.4-adoption.md)

---

## Change Log

| Date | Change |
|------|--------|
| 2026-11-18 | **Renamed and split** (roadmap reset): `prd-v2.5.5-formal-spec-interop.md` → `prd-spec-profile.md` (old path is a redirect stub). Target **v2.7.0**, after Interop. Scope narrowed to ASAP's unique surface, written as a profile on A2A / MCP / OAuth. Added SPEC-010 (every MUST → `asap-compliance` check) and SPEC-011 (A2A / MCP profile sections). SPEC-009 now covers HTTP MCP. COMPAT-001..005 moved to [prd-interop.md](./prd-interop.md). TSOA stays default-defer. |
| 2026-07-18 | Dist Loop **shipped** (tag `v2.5.4`); soft Dist inputs marked available for Spec kickoff |
| 2026-07-18 | Hard vs soft prerequisites; Dist handoff paths; TSOA default defer; non-goals vs Dist OOS |
| 2026-07-08 | Renumbered v2.5.3 → **v2.5.5** (train shift after v2.5.2 security ship) |
| 2026-06-22 | Split from `prd-v2.4-adoption.md` §4.2–4.6; renumbered to v2.5.3 |
