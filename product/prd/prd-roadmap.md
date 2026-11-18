# PRD: ASAP Protocol — Roadmap

> **Product Requirements Document (living train index)**
>
> **Version**: living index (not tied to one library version)
> **Status**: ACTIVE
> **Created**: 2026-11-18
> **Last Updated**: 2026-11-18
>
> **Current library**: `asap-protocol` **2.5.11** — release history lives in [`CHANGELOG.md`](../../CHANGELOG.md) and [GitHub Releases](https://github.com/asap-protocol/asap-protocol/releases)
> **Replaces**: [prd-v2.5-roadmap.md](./prd-v2.5-roadmap.md) (v2.5.x train index, **closed** 2026-11-18). From now on this file is the only public train index. New trains do not get their own index file.

---

## 1. Why the reset

v2.5.0–v2.5.4 shipped the planned v2.5 scope: MCP Auth Bridge, a code quality patch, a security follow-up, Adapter Lab II, and the Distribution Loop. After that, seven patches (2.5.5–2.5.11) shipped maintenance only: security fixes, dependency floors, and regression tests. None of them shipped Formal Spec.

The next planned train kept its `v2.5.5` label while the library moved past that number. Planned scope and patch numbers drifted apart, and status docs grew one row per patch.

The reset fixes this:

1. **The v2.5.x train is closed at 2.5.11.** No more planned scope ships on 2.5.x. A 2.5.12 ships only if a security fix requires it, under the release policy (§3).
2. **Planned scope ships only in minor releases** (`x.Y.0`). Patches are unplanned maintenance.
3. **PRDs are named by track, not by version** (`prd-interop.md`, `prd-spec-profile.md`). The target version lives in the PRD header, so a slip changes one field, not a filename.
4. **This file is the single public train index.**

---

## 2. Roadmap

| Phase | Version | Track / PRD | Scope | Exit criteria | Status |
|-------|---------|-------------|-------|---------------|--------|
| **0 — Reset** | none (2.5.12 only if a CVE requires it) | — (this file) | Close the v2.5 train. Publish the release policy and the integration support tiers (§3). Restore weekly adoption telemetry ([runbook](../../docs/maintainers/telemetry.md)). Triage the automated regression-test PRs. Find 1–3 external design partners. | Telemetry baseline collected; policy and tiers published | **Active** |
| **Interop** | **v2.6.0** | [prd-interop.md](./prd-interop.md) | **P1** A2A v1.0 Agent Card ↔ ASAP manifest bridge, including signed-card verification (COMPAT-005/006). **P1** accept external agent tokens as ASAP agent sessions, including MCP OAuth 2.1 bearer tokens (COMPAT-001/002). **P1** `@asap-protocol/mcp-auth` for HTTP MCP servers (MCP-TS-001..003). **P2** discovery bridge (COMPAT-003). **MUST** interoperability guide (COMPAT-004). Housekeeping: remove the six import paths deprecated in v2.5.1 for v2.6.0, including the `asap.cli._compat` shim ([#275](https://github.com/asap-protocol/asap-protocol/issues/275)) (HK-001..HK-007). | One A2A↔ASAP flow and one MCP-HTTP↔ASAP flow work end to end, each with compliance checks | **Next** — PLANNED |
| **Spec profile** | **v2.7.0** | [prd-spec-profile.md](./prd-spec-profile.md) | RFC 2119 spec scoped to ASAP's unique surface (identity, capability grants, approval, lifecycle, error registry), written as a profile on A2A / MCP / OAuth. Security and privacy considerations (PRIV-*). Capability-aware introspection (INTRO-*). Each MUST maps to an `asap-compliance` check. TSOA (`@asap-protocol/openapi`) stays default-defer. | Spec published; compliance covers 100% of MUST statements | PLANNED (after Interop) |
| **Decision gate** | — | this file | Review adoption data after v2.6.0 and v2.7.0, then decide the v3 direction | Decision recorded | After v2.7.0 |
| **Economy** | product track (not library v3.0.0) | [prd-v3.0-economy.md](./prd-v3.0-economy.md) | Stays a trigger-gated VISION DRAFT. Open question: evaluate interop with the A2A AP2 payments extension before building a separate settlement layer or clearing house. | Triggers unchanged | VISION DRAFT |

**Why Interop comes before Spec:** the spec profile describes how ASAP layers on A2A and MCP. If the bridges ship first, the spec documents shipped behavior. If the spec comes first, it risks rework.

**Phase 0 has no PRD and no planned release.** Its outputs are process and evidence: the policy, the tiers, a telemetry baseline for the decision gate, and partners who can test the Interop flows. Telemetry values stay in maintainer-only files. This index does not publish them.

---

## 3. Release policy (summary)

The full policy is in [docs/maintainers/release-policy.md](../../docs/maintainers/release-policy.md). Summary:

| # | Rule |
|---|------|
| 1 | Planned scope ships only in minor releases (`x.Y.0`). Patches (`x.y.Z`) are unplanned maintenance: security fixes, dependency floors, correctness bugs. Patches never carry planned PRD scope, never get a codename, and never add endpoints, models, or protocol surface. |
| 2 | Test-only changes do not trigger a release. Regression-fence PRs merge to `main` and ship with the next release that needs one. |
| 3 | Dependency floors are batched into at most one maintenance patch per month. Exception: an advisory that is exploitable through ASAP's shipped code paths uses the [hotfix path](../../docs/maintainers/hotfix.md). |
| 4 | Release history lives in `CHANGELOG.md` and GitHub Releases only. Status docs carry the current version, the next train, and a link to the changelog. No per-patch rows. |
| 5 | New PRDs are named by track, not by version. The target version goes in the PRD header. This file is the train index. |
| 6 | Library major **v3.0.0** is reserved for breaking protocol or wire changes. Commercial services (economy, billing, hosting) are product tracks, decoupled from library semver. |

### 3.1 Integration support tiers

The full classification and the promotion rules are in [docs/integrations/support-tiers.md](../../docs/integrations/support-tiers.md). Summary:

| Tier | Promise | Includes (examples) |
|------|---------|---------------------|
| **Tier 1 — Core** | Security patch cadence; CI-gated; breaking changes only in minors, with deprecation | Python SDK core (models, transport, auth, discovery, state), MCP Auth Bridge, OpenAPI adapter, `@asap-protocol/client`, `asap-compliance` |
| **Tier 2 — Maintained** | Tested in CI; security fixes best effort in the next maintenance patch | Python framework integrations and workflow connectors — see the classification table |
| **Tier 3 — Experimental** | No compatibility promise; may be removed in a minor after one release of deprecation notice | Integrations that did not meet the promotion gate (3+ `adapter-request` issues or 1 partner commitment) or are already labeled experimental — for example `@asap-protocol/mastra`, `@asap-protocol/openai-agents`, Microsoft Agent Framework, NeMo Agent Toolkit |

---

## 4. Positioning

ASAP is the **capability, approval, and compliance layer for agents**. It interoperates with A2A (v1.0, signed Agent Cards) and MCP (authorization based on OAuth 2.1). It tracks emerging IETF agent identity work (WIMSE/AIMS and OAuth agent drafts). It does not replace A2A or MCP.

### 4.1 Federated stack

ASAP reuses the base standard at each layer and adds identity, policy, and conformance on top.

```text
Layer           Base standard (reused)              What ASAP adds
--------------  ----------------------------------  ------------------------------------------------
L4 Interaction  A2A v1.0 tasks and messages         ASAP envelope + task state machine (v2.2+)
L3 Execution    MCP tools                           MCP Auth Bridge: Agent JWT + grants on tools/call
                                                    (stdio v2.5.0; HTTP planned v2.6.0)
L2 Discovery    A2A Agent Card                      Manifest, well-known, Lite Registry (v2.3+);
                                                    Agent Card bridge planned v2.6.0
L1 Identity     OAuth 2.1 / OIDC; MCP authorization Host/Agent JWT, capability grants, approval
                                                    flows (v2.2+); external-token mapping v2.6.0
Cross-cutting   —                                   asap-compliance conformance checks
```

### 4.2 ASAP's unique surface

| Surface | Shipped in | Spec profile covers it |
|---------|-----------|------------------------|
| Constraint-based capability grants | v2.2 | Yes |
| Approval flows (device authorization, CIBA, WebAuthn, A2H) | v2.2 | Yes |
| Per-runtime agent identity (Host/Agent JWT) | v2.2 | Yes |
| Compliance harness (`asap-compliance`) | v1.2 | Yes — each MUST maps to a check |

The spec profile (v2.7.0) specifies this surface and cites A2A, MCP, and OAuth for everything else.

### 4.3 Wording rule

In public copy, say "A2A v1.0" and "MCP authorization (OAuth 2.1)". Do not claim exact external dates or statuses that the repo cannot verify. Do not present ASAP as "A2A but better" or as an MCP replacement.

---

## 5. Parked and deferred items

Carried over from [prd-v2.5-roadmap.md](./prd-v2.5-roadmap.md) §2 "Parked / orphan owners", with owners updated on 2026-11-18.

| Item | Owner | Rule |
|------|-------|------|
| `@asap-protocol/mcp-auth` (npm, HTTP MCP servers) | [prd-interop.md](./prd-interop.md) — MCP-TS-001..003 | **P1** in v2.6.0 (was the [v2.5.0 backlog](../../engineering/tasks/v2.5.0/backlog-mcp-auth-typescript.md)) |
| Remove `asap.cli._compat` ([#275](https://github.com/asap-protocol/asap-protocol/issues/275)) and the other five v2.6.0 deprecations | [prd-interop.md](./prd-interop.md) — housekeeping HK-001..HK-007 | Ships in v2.6.0 ([#275 task file](../../engineering/tasks/deferred/issue-275-cli-compat-removal-v2.6.0.md)) |
| `@asap-protocol/openapi` (TSOA) | [prd-spec-profile.md](./prd-spec-profile.md) §3.5 | **Default defer** unless there is demand at Spec kickoff |
| Fourth starter (workflow) | Optional | Lab II `examples/workflow_asap_connector/` is already public |
| Registry API (PostgreSQL) | Trigger: 500 agents / IssueOps bottleneck | Deferred; not a silent prerequisite of any track |
| Design System Revamp | Separate design track ([prd-design-system-revamp.md](./prd-design-system-revamp.md)) | Not part of a library train |
| `create-asap` / scaffold CLI | If demanded | Not scheduled |
| Public metrics dashboard UI | — | Out; maintainer telemetry only |

---

## 6. Non-goals (all tracks, unless a track PRD says otherwise)

| Feature | When |
|---------|------|
| Economy / billing / hosting | Economy product track (trigger-gated); not library v3.0.0 |
| Federated registry | Not scheduled; revisit at the decision gate |
| gRPC binding | TBD |
| Schema negotiation runtime (Agora-style) | Out of scope |
| Registry API backend (PostgreSQL) | Deferred until the registry trigger fires |
| Replacing A2A or MCP | Never — ASAP layers on them (§4) |
| Planned scope in a patch release | Never — see §3 rule 1 |

---

## 7. Related documents

- **Release policy**: [docs/maintainers/release-policy.md](../../docs/maintainers/release-policy.md)
- **Integration support tiers**: [docs/integrations/support-tiers.md](../../docs/integrations/support-tiers.md)
- **Hotfix path**: [docs/maintainers/hotfix.md](../../docs/maintainers/hotfix.md)
- **Telemetry runbook**: [docs/maintainers/telemetry.md](../../docs/maintainers/telemetry.md)
- **Interop (v2.6.0)**: [prd-interop.md](./prd-interop.md)
- **Spec profile (v2.7.0)**: [prd-spec-profile.md](./prd-spec-profile.md)
- **Economy (product track)**: [prd-v3.0-economy.md](./prd-v3.0-economy.md)
- **Closed v2.5.x train**: [prd-v2.5-roadmap.md](./prd-v2.5-roadmap.md)
- **PRD index**: [README.md](./README.md)
- **Tasks**: [engineering/tasks/README.md](../../engineering/tasks/README.md)
- **Architecture decisions**: [product/decision-records/](../decision-records/README.md)

---

## Change Log

| Date | Change |
|------|--------|
| 2026-11-18 | **Created** as the living train index. v2.5.x closed at 2.5.11. Release policy summary and support tiers. Roadmap: Phase 0 Reset → v2.6.0 Interop → v2.7.0 Spec profile → decision gate; Economy becomes a product track. Positioning (federated stack on A2A / MCP / OAuth). Parked items and non-goals carried over from [prd-v2.5-roadmap.md](./prd-v2.5-roadmap.md) with updated owners. |
