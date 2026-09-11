# Documentation Checkpoints

> **Purpose**: Formal review points to update documentation with learnings (product follow-up after releases).
> **Location**: Lives under **`product/`** because it drives PRD updates and retros, not day-to-day engineering execution.
> **Created**: 2026-02-06
> **Updated**: 2026-09-11 — **v2.5.5** security/quality patch **shipped** (tag [`v2.5.5`](https://github.com/asap-protocol/asap-protocol/releases/tag/v2.5.5); PyPI **2.5.5**; [#351](https://github.com/asap-protocol/asap-protocol/pull/351)). Formal Spec remains **next** ([prd-v2.5.5-formal-spec-interop.md](./prd/prd-v2.5.5-formal-spec-interop.md) — filename historical, not this library tag). Long-term = **v3.0** Economy (trigger-gated).

---

## Status roll-up (verified in-repo)

Use this section first; the checkpoint sections below add detail or archive.

**Evidence snapshot** (refresh with `git log` and [`pyproject.toml`](../pyproject.toml)):
- **Shipped (2026-09-11):** `pyproject.toml` **`version = "2.5.5"`** on `main` (security/quality patch — **not** Formal Spec). Tag [`v2.5.5`](https://github.com/asap-protocol/asap-protocol/releases/tag/v2.5.5); merge [#351](https://github.com/asap-protocol/asap-protocol/pull/351). Scope: `CHANGELOG.md` `[2.5.5]`.
- **Shipped (2026-07-18):** `pyproject.toml` **`version = "2.5.4"`** on `main` (Distribution Loop). Tag [`v2.5.4`](https://github.com/asap-protocol/asap-protocol/releases/tag/v2.5.4); PyPI `asap-protocol==2.5.4`; merge [#294](https://github.com/asap-protocol/asap-protocol/pull/294). Scope: [prd-v2.5.4-distribution-loop.md](./prd/prd-v2.5.4-distribution-loop.md).
- **Shipped (2026-07-16):** `pyproject.toml` **`version = "2.5.3"`** on `main` (Adapter Lab II). Tag [`v2.5.3`](https://github.com/asap-protocol/asap-protocol/releases/tag/v2.5.3); PyPI `asap-protocol==2.5.3`; merge [#291](https://github.com/asap-protocol/asap-protocol/pull/291). Scope: [prd-v2.5.3-adapter-lab-ii.md](./prd/prd-v2.5.3-adapter-lab-ii.md).
- **Shipped on `main` (2026-07-08):** `pyproject.toml` was **`2.5.2`**. Tag [`v2.5.2`](https://github.com/asap-protocol/asap-protocol/releases/tag/v2.5.2) (security & correctness follow-up, merge [#281](https://github.com/asap-protocol/asap-protocol/pull/281)); PyPI `asap-protocol` **2.5.2**. Umbrella [#209](https://github.com/asap-protocol/asap-protocol/issues/209) closed. Scope: [prd-v2.5.2-security-follow-up.md](./prd/prd-v2.5.2-security-follow-up.md).
- **Prior:** [`v2.5.1`](https://github.com/asap-protocol/asap-protocol/releases/tag/v2.5.1) code quality patch (2026-06-26); [`v2.5.0`](https://github.com/asap-protocol/asap-protocol/releases/tag/v2.5.0) MCP Auth Bridge; [`v2.5.0.1`](https://github.com/asap-protocol/asap-protocol/releases/tag/v2.5.0.1) compliance-only → `asap-compliance` **1.3.0**. npm `@asap-protocol/*` **2.4.1**.
- **v2.4.1** security hardening patch shipped **2026-06-14** (tag [`v2.4.1`](https://github.com/asap-protocol/asap-protocol/releases/tag/v2.4.1)).
- **v2.4.0** edge-AI discovery shipped **2026-05-24** ([`CHANGELOG.md`](../CHANGELOG.md#240---2026-05-24)).
- **S1 OpenAPI adapter** (v2.3.0): first package commit **`cedb3f9`** — `2026-05-01` 19:16 -0300; merge **`04c56be`** — `2026-05-02` 01:03 -0300 (`#132`). Review: [`engineering/code-review/v2.3.0/pr-132-openapi-adapter.md`](../engineering/code-review/v2.3.0/pr-132-openapi-adapter.md).

| Track | Done (shipped / decided) | Still open (ahead) |
|-------|--------------------------|-------------------|
| **v1.1.0 → v1.4.0** | Releases on the v1.x train shipped per PRDs; Lean Marketplace pivot absorbed into planning (2026-02). CP-1–CP-4 checklists below marked **`[x]`** (milestone closed). | Standalone retros (`v1.1.0-retro` …) may still be missing on disk — only [`v1.0.0-retro.md`](../engineering/lessons-learned/v1.0.0-retro.md) exists **today**; add files if you want them archived. |
| **v2.0.0** | Web app + marketplace usage foundation delivered (M1–M4 era per task docs); protocol moved on through v2.1+. | CP-5/CP-6 “two weeks after launch” metrics + `vision-v2.1.md`-style follow-ups were **not** finalized here; treat as **optional** unless you revisit launch analytics. |
| **v2.1.x / v2.2.x / v2.2.1** | **Released** per [tasks-v2.3.0-adoption-multiplier.md](../engineering/tasks/v2.3.0/tasks-v2.3.0-adoption-multiplier.md) prerequisites (v2.2.0 **2026-04-15**, v2.2.1 **2026-04-21**). No dedicated checkpoint section existed in this doc — learnings live in PRDs, ADRs, and [`engineering/code-review/`](../engineering/code-review/). | Optional: one consolidated retro doc if you want a single narrative for the v2.2 cycle. |
| **v2.3.0 Adoption Multiplier** | **S1** merged (**#132**, commit `04c56be`). Subsequent v2.3.x / v2.4.0 trains shipped on the timeline below. | Optional: consolidated v2.3 retro if you want a single narrative. |
| **v2.4.0 — Edge-AI discovery** | **Released** **2026-05-24** — hardware/inference manifest fields, registry mirror, marketplace filters, ShellClaw onboarding ([`CHANGELOG.md`](../CHANGELOG.md#240---2026-05-24)). | Community enum feedback: [#176](https://github.com/asap-protocol/asap-protocol/issues/176). |
| **v2.4.1 — Security hardening** | **Released** **2026-06-14** — OAuth2 `iss`/`aud` validation, fail-closed identity binding, web SSRF/redirect hardening, dependency bumps ([`CHANGELOG.md`](../CHANGELOG.md#241---2026-06-14), [migration](../docs/migration.md#upgrading-from-v240-to-v241)). Tag **v2.4.1**. | Deferred §4 items shipped in **v2.5.2**; [#209](https://github.com/asap-protocol/asap-protocol/issues/209) **closed**. |
| **v2.5.0 — MCP Auth Bridge** | **Released** **2026-06-24** — `asap.adapters.mcp` (`protect_server`), stdio JWT carriage, compliance profile `mcp-auth-bridge`, [adapter guide](../docs/adapters/mcp-auth-bridge.md) ([`CHANGELOG.md`](../CHANGELOG.md#250---2026-06-24), [migration](../docs/migration.md#upgrading-from-v241-to-v250), [PRD](./prd/prd-v2.5.0-mcp-auth-bridge.md)). Tag **v2.5.0**; PyPI/Docker via [sprint-S5-release.md](../engineering/tasks/v2.5.0/sprint-S5-release.md). | **`asap-compliance` 1.3.0** on PyPI (tag **v2.5.0.1**); `@asap-protocol/mcp-auth` ([backlog](../engineering/tasks/v2.5.0/backlog-mcp-auth-typescript.md)). |
| **v2.5.1 — Code quality patch** | **Released** **2026-06-26** — behavior-preserving refactor + six P0 fixes. PR [#244](https://github.com/asap-protocol/asap-protocol/pull/244). Tag **v2.5.1**. ([`CHANGELOG.md`](../CHANGELOG.md#251---2026-06-25), [migration](../docs/migration.md#upgrading-from-v250-to-v251)). | Follow-ups #245–#249 **closed** in v2.5.2; Adapter Lab II → **v2.5.3**. |
| **v2.5.2 — Security follow-up** | **Released** **2026-07-08** — #209 (operator auth, `extra="forbid"`, Redis JTI, web rate limits) + CR #245–#249 + registry #224/#227 + deps #258. PR [#281](https://github.com/asap-protocol/asap-protocol/pull/281); tag **v2.5.2**; PyPI **2.5.2**. [PRD](./prd/prd-v2.5.2-security-follow-up.md), [`CHANGELOG`](../CHANGELOG.md#252---2026-07-08), [migration](../docs/migration.md#upgrading-from-v251). | CP-7 for 2.5.2. |
| **v2.5.3 — Adapter Lab II** | **Released** **2026-07-16** — workflow connectors, automation security, experimental MAF / NAT guides, DX fixes. Tag [`v2.5.3`](https://github.com/asap-protocol/asap-protocol/releases/tag/v2.5.3); PyPI **2.5.3**; PR [#291](https://github.com/asap-protocol/asap-protocol/pull/291). [PRD](./prd/prd-v2.5.3-adapter-lab-ii.md), [`CHANGELOG`](../CHANGELOG.md#253---2026-07-14), [migration](../docs/migration.md#upgrading-from-v252-to-v253); tasks [tasks-v2.5.3-roadmap.md](../engineering/tasks/v2.5.3/tasks-v2.5.3-roadmap.md). | Optional Lab II retro; fourth starter not required. |
| **v2.5.4 — Distribution Loop** | **Released** **2026-07-18** — thin starters, Build for agents guide, homepage agent-first CTAs, telemetry ops. Tag [`v2.5.4`](https://github.com/asap-protocol/asap-protocol/releases/tag/v2.5.4); PyPI **2.5.4**; PR [#294](https://github.com/asap-protocol/asap-protocol/pull/294). [PRD](./prd/prd-v2.5.4-distribution-loop.md), [`CHANGELOG`](../CHANGELOG.md#254---2026-07-18), [migration](../docs/migration.md#upgrading-from-v253-to-v254); tasks [tasks-v2.5.4-roadmap.md](../engineering/tasks/v2.5.4/tasks-v2.5.4-roadmap.md). | Handoff → Spec §11; optional Dist metrics → v3.0 proxies. |
| **v2.5.5 — Security & quality patch** | **Released** **2026-09-11** — OpenAPI path-param climb, registry URN overwrite guard, marketplace SSRF DNS pin, capability-replace consent, fail-closed reactivate, Agent JWT persist/revoke races. PR [#351](https://github.com/asap-protocol/asap-protocol/pull/351); tag [`v2.5.5`](https://github.com/asap-protocol/asap-protocol/releases/tag/v2.5.5). [`CHANGELOG`](../CHANGELOG.md#255---2026-09-11), [migration](../docs/migration.md#upgrading-from-v254-to-v255). **Not** Formal Spec. | Formal Spec remains next (historical PRD filename). |
| **After library 2.5.5** | Soft: Formal Spec ([prd-v2.5.5-formal-spec-interop.md](./prd/prd-v2.5.5-formal-spec-interop.md) — filename historical). Long-term: Economy → **v3.0** (triggers). | Create `engineering/tasks/v2.5.5/` at Spec kickoff; do not start Economy without triggers. |

**Sources of truth for current execution**: [prd-v2.5-roadmap.md](./prd/prd-v2.5-roadmap.md), [prd-v2.5.5-formal-spec-interop.md](./prd/prd-v2.5.5-formal-spec-interop.md) (next; filename historical), shipped Dist artifacts in [prd-v2.5.4-distribution-loop.md](./prd/prd-v2.5.4-distribution-loop.md) / [tasks-v2.5.4-roadmap.md](../engineering/tasks/v2.5.4/tasks-v2.5.4-roadmap.md), [AGENTS.md](../AGENTS.md).

---

## Why Checkpoints?

As a solo developer, it's easy to lose context between sprints. These checkpoints ensure:
1. **Lessons captured**: What worked, what didn't
2. **Docs updated**: Next version PRDs refined with learnings  
3. **Estimates adjusted**: Realistic planning based on actual velocity
4. **Context preserved**: Knowledge carries forward across releases (v2.3.x adoption train and beyond)

---

## Checkpoint Schedule

### CP-1 — CP-6 (v1.1.0 through v2.0.0 M4)

These checkpoints were defined during the v1.x / v2.0 marketplace push. **All associated releases have shipped** on the project timeline. Checklist items below are marked **`[x]`** to record that the milestone is **closed** (work absorbed by subsequent releases, ADRs, and PRDs; some optional retros were never written as separate files).

<details>
<summary><strong>CP-1: Post v1.1.0 Release</strong> (archive)</summary>

**When**: After v1.1.0 ships (release preparation done 2026-02-10; publish in Task 4.7).

Pre-release: Release materials (CHANGELOG, README, AGENTS.md, secure_agent example) completed. Full review and velocity update to be done after v1.1.0 ships.

**Review**:
- [x] OAuth2 implementation complexity — update v1.2 estimates?
- [x] WebSocket lessons learned
- [x] Discovery patterns that worked
- [x] State Storage Interface (SD-9): Did SQLite impl meet needs? Is `MeteringStore` interface sufficient for v1.3?
- [x] Agent Liveness (SD-10): Is the health endpoint effective? Is `ttl_seconds` default (300s) appropriate?
- [x] `SnapshotStore` sync vs async: Should we evolve to async Protocol? (Breaking change analysis)
- [x] Lite Registry (SD-11, ADR-15): Is GitHub Pages adequate? Is the multi-endpoint schema flexible enough?
- [x] WebSocket MessageAck (ADR-16): Is `AckAwareClient` timeout (30s default) appropriate? Retransmission working correctly?
- [x] Custom Claims binding (ADR-17): Are Custom Claims practical with all IdPs? Is the allowlist fallback needed often?
- [x] Best Practices Failover doc: Is the `StateQuery`/`StateRestore` pattern sufficient without formal `TaskHandover` payload?
- [x] Time taken vs estimated

**Update**:
- [x] `prd-v1.2-roadmap.md` — refine based on auth + storage learnings
- [x] `prd-v1.3-roadmap.md` — confirm `MeteringStore` interface meets v1.3 needs
- [x] `tasks-v1.2.0-roadmap.md` — adjust estimates (now 4 sprints / 13 tasks post-Lean pivot)
- [x] `lessons-learned/v1.1.0-retro.md` — create retrospective

</details>

<details>
<summary><strong>CP-2: Post v1.2.0 Release</strong> (archive)</summary>

**When**: After v1.2.0 ships

**Review**:
- [x] Ed25519 PKI complexity
- [x] Compliance harness effectiveness — is it usable by third parties?
- [x] Lite Registry (SD-11): Is GitHub Pages still sufficient? Should Registry API Backend be accelerated for v2.0?
- [x] Time taken vs estimated

**Update**:
- [x] `prd-v1.3-roadmap.md` — refine observability based on PKI learnings
- [x] `tasks-v1.3.0-roadmap.md` — adjust estimates (now 3 sprints / 13 tasks post-Lean pivot)
- [x] `lessons-learned/v1.2.0-retro.md` — create retrospective

> [!NOTE]
> Registry API review item removed (deferred to v2.1). Compliance harness is now the primary deliverable to evaluate.

</details>

<details>
<summary><strong>CP-3: Post v1.3.0 Release</strong> (archive)</summary>

**When**: After v1.3.0 ships

**Status**: v1.3.0 released (2026-02-18). Tag v1.3.0 pushed; Sprints E1–E3 done (Metering, Delegation, SLA Framework). Checkpoint review ready.

**Review**:
- [x] Observability metering implementation lessons
- [x] Delegation token complexity
- [x] SLA framework effectiveness
- [x] Lite Registry capacity — can it handle target 100+ agents for v2.0?

**Update**:
- [x] `prd-v2.0-roadmap.md` — major update with all v1.x learnings
- [x] `tasks-v2.0.0-roadmap.md` — realistic estimates (now 4 sprints / 20 tasks post-Lean pivot)
- [x] `lessons-learned/v1.3.0-retro.md` — create retrospective
- [x] Web App tech stack decisions (Next.js + Lite Registry)
- [x] `v2.0-marketplace-usage-foundation.md` — refine with E1/E2/E3 implementation learnings

</details>

<details>
<summary><strong>CP-4: Post v1.4.0 Release</strong> (archive)</summary>

**When**: After v1.4.0 ships

**Review**:
- [x] Type safety impact (mypy checks, runtime stability)
- [x] Memory usage with paginated queries (SLA history)
- [x] Developer experience with stricter types

**Update**:
- [x] `prd-v2.0-roadmap.md` — confirm "Hardening" sprint met v2.0 scale needs
- [x] `tasks-v2.0.0-roadmap.md` — adjust final v2.0 estimate

</details>

<details>
<summary><strong>CP-5: Post v2.0.0 M3 (Developer Experience)</strong> (archive)</summary>

**When**: After completing sprints M1–M3

**Status**: M3 complete (2026-02-21). IssueOps registration + verification flows implemented. Ready for M4 launch prep.

**Review**:
- [x] Lite Registry data layer — is `registry.json` SSG/ISR working well?
- [x] Web App performance with static registry data
- [x] Client-side search/filter UX
- [x] IssueOps flow — Web Form → GitHub Issue → Action: friction level acceptable?
- [x] Verification request flow — form → pre-filled Issue: working as expected?
- [x] NextAuth scope downgrade (`read:user` only) — any missing permissions?

**Update**:
- [x] `sprint-M4-launch-prep.md` — adjust estimates based on M3 velocity
- [x] Evaluate if Registry API Backend should be built for v2.1

**Reference**: [sprint-M3-developer-experience.md](../engineering/tasks/v2.0.0/sprint-M3-developer-experience.md)

</details>

<details>
<summary><strong>CP-6: Post v2.0.0 M4 (Launch)</strong> (archive)</summary>

**When**: 2 weeks after launch

**Review**:
- [x] Launch success metrics (per PRD: 100+ agents, 500+ weekly visits)
- [x] User feedback
- [x] What to do next (v2.1 Registry API Backend? v2.2 DeepEval?)

**Create**:
- [x] `lessons-learned/v2.0.0-retro.md` — comprehensive retrospective
- [x] `vision-v2.1.md` — Registry API Backend + deferred features roadmap

</details>

---

### CP-7: Post v2.3.x Adoption train (documentation & learning)

**When**: After **v2.3.0** ships (S5 complete), or mid-flight if you run a **beta** period before tagging. **Partial closure (2026-06-26)**: **v2.5.1** code quality patch shipped; README, `docs/index.md`, CHANGELOG, AGENTS.md, `apps/web` WhatsNewRibbon, and `engineering/tasks/README.md` reflect **2.5.1** / **`asap-compliance` 1.3.0** (tag `v2.5.0.1`). v2.5.1 follow-ups filed (#245–#249).

**Why**: v2.3.0 rescoped to adoption (OpenAPI, TS SDK, auto-registration, escalation/challenge). This checkpoint captures whether docs and PRDs reflect reality—not registry scale gates.

**Review** (pick what applies once artifacts exist):
- [ ] OpenAPI adapter: DX for Python + framework authors; PetStore / examples sufficient?
- [ ] TypeScript SDK: provider adapters (Vercel AI / OpenAI / Anthropic) — gaps vs npm consumers?
- [ ] Auto-registration: IssueOps vs `POST /registry/agents` — friction, abuse, harness integration?
- [ ] Escalation + `WWW-Authenticate` challenge path — observability and support burden?
- [ ] Coverage / quality bar met for new modules (Python + TS) per Definition of Done?
- [ ] Time taken vs estimated (S1–S5); parallelism assumptions validated?
- [ ] **v2.5.1 code-quality patch**: did the audit-driven refactor hold up under the PR #244 code review? Follow-ups #245–#249 closed in **v2.5.2** — any residual debt?
- [ ] **v2.5.2 security follow-up**: did #209 + registry fixes land cleanly? Close umbrella #209 after tag.

**Update**:
- [ ] `prd-v2.3-scale.md` — ship checklist vs actual scope; note v2.3.1 shipped; v2.3.2/2.3.3 → **v2.5.3/v2.5.4** (2026-07-08 rescope)
- [x] `CHANGELOG.md` + public docs — README + `docs/index.md` + `docs/migration.md` aligned with shipped surface (**v2.5.1**, 2026-06-26)
- [x] `apps/web` homepage / WhatsNew — ribbon reflects **v2.5.1**; MCP Auth Bridge kept as Hero/marquee (v2.5.1 is a quality patch, not a feature release)
- [ ] Optional: `engineering/lessons-learned/v2.3.0-retro.md` — short retrospective (hypothesis: adoption flywheel vs 500-agent trigger)
- [ ] Feed learnings into [prd-v2.5-roadmap.md](./prd/prd-v2.5-roadmap.md) and [prd-v2.5.0-mcp-auth-bridge.md](./prd/prd-v2.5.0-mcp-auth-bridge.md)

**Reference**: [tasks-v2.3.0-adoption-multiplier.md](../engineering/tasks/v2.3.0/tasks-v2.3.0-adoption-multiplier.md)

---

## Checkpoint Process

For each checkpoint:

1. **Take 30-60 minutes** to review honestly
2. **Update documents** while context is fresh
3. **Adjust estimates** based on actual velocity  
4. **Capture patterns** that can be reused
5. **Commit changes** with descriptive message

---

## Velocity Tracking

Track actual vs estimated to improve future planning:

| Version | Estimated Days | Actual Days | Velocity |
|---------|----------------|-------------|----------|
| v1.1.0 | 31-41 | — | — |
| v1.2.0 | 18-26 | — | — |
| v1.3.0 | 15-22 | ~12 | On track (2026-02 note) |
| v1.4.0 | 5-8 | — | — |
| v2.0.0 | 23-33 | — | — |
| v2.1.x | — | — | — |
| v2.2.x / v2.2.1 | — | — | Shipped 2026-04-15 / 2026-04-21 |
| v2.4.0 | — | — | Shipped 2026-05-24 |
| v2.4.1 | — | ~0.5 day (S2) | Shipped 2026-06-14 (security patch) |
| v2.5.0 | — | — | Shipped 2026-06-24 (MCP Auth Bridge; S0–S5) |
| v2.5.1 | 0.5 day (S4 est.) | ~1.5 days (S4 + code review fixes) | Shipped 2026-06-26 (code quality patch; code-quality audit S0–S3 + P0 fixes + PR #244 review) |

> [!NOTE]
> Estimates updated after Lean Marketplace pivot: v1.2 reduced (6→4 sprints), v1.3 reduced (4→3 sprints), v2.0 reduced (6→4 sprints). Fill **Actual** when you reconcile from sprint notes.

---

## Related Documents

- [PRD v2.5.x train](./prd/prd-v2.5-roadmap.md)
- [PRD v2.5.4 — Distribution Loop](./prd/prd-v2.5.4-distribution-loop.md) · [tasks](../engineering/tasks/v2.5.4/tasks-v2.5.4-roadmap.md)
- [PRD Formal Spec (filename `prd-v2.5.5-formal-spec-interop.md`)](./prd/prd-v2.5.5-formal-spec-interop.md)
- [PRD v3.0 — Economy](./prd/prd-v3.0-economy.md)
- [PRD v2.5.0 — MCP Auth Bridge](./prd/prd-v2.5.0-mcp-auth-bridge.md)
- [PRD v2.3 — Adoption multiplier](./prd/prd-v2.3-scale.md)
- [PRD v2.0 — Marketplace roadmap](./prd/prd-v2.0-roadmap.md)
- [Product strategy ADRs (deferrals & pivots)](./decision-records/05-product-strategy.md)
- [lessons-learned/](../engineering/lessons-learned/)

---

## Change Log

| Date | Change |
|------|--------|
| 2026-02-06 | Initial checkpoints document |
| 2026-02-12 | **Lean Marketplace pivot**: Updated CP-2 (removed Registry API review), CP-3 (metering→observability), CP-4 (renamed from "Marketplace Core" to "Web App Features"), CP-5 (merged with CP-6, updated metrics). Reduced from 6 checkpoints to 5. Updated velocity estimates. |
| 2026-02-18 | **v1.3.0 release prep**: CP-3 status updated (Sprints E1–E3 complete, PR #50 open). Velocity: v1.3.0 ~12 days actual vs 15–22 estimated. |
| 2026-02-18 | **v1.3.0 released**: Tag v1.3.0 pushed. CP-3 checkpoint review ready. |
| 2026-02-21 | **Sprint M3 complete**: CP-5 updated for M1–M3 (IssueOps, verification flow). Launch prep next. |
| 2026-04-28 | **Relocated** from `engineering/checkpoints.md` to `product/checkpoints.md` as product-level documentation follow-up (links updated). |
| 2026-04-28 | **Status roll-up**: Table for shipped vs ahead; CP-1–CP-6 folded under `<details>` as archive; **CP-7** added for v2.3.x adoption; honesty note on missing retros; velocity rows for v2.2.x. |
| 2026-05-02 | **Repo-verified evidence**: `pyproject.toml` still **2.2.1**; S1 merge **`04c56be`** (2026-05-02 01:03 -0300), first adapter commit **`cedb3f9`** (2026-05-01); linked PR-132 review date. |
| 2026-05-02 | **Archive checklists**: All CP-1–CP-6 items set to **`[x]`** (milestone closed); CP-7 remains **`[ ]`** until post–v2.3.0 review. |
| 2026-06-22 | **v2.5.x PRD train**: Rescoped adoption PRD → v2.5.0–v2.5.3; v2.3.2/2.3.3 → v2.5.1/2.5.2; deprecated `prd-v2.4-adoption.md`. |
| 2026-06-24 | **v2.5.0 ship + doc sync**: Status roll-up for MCP Auth Bridge; README, `docs/index.md`, AGENTS.md aligned with **2.5.0**; CP-7 partial closure updated. |
| 2026-06-26 | **v2.5.1 ship + doc sync**: Status roll-up for the code-quality patch (code-quality audit S0–S3 + six P0 fixes); README, `docs/index.md`, `docs/migration.md`, AGENTS.md, `apps/web` WhatsNewRibbon, `engineering/tasks/README.md` aligned with **2.5.1**; Adapter Lab II slipped to v2.5.2; follow-ups filed (#245–#249); velocity row for v2.5.1 added. |
| 2026-07-08 | **Train rescope**: v2.5.2 = security follow-up; Adapter Lab II → v2.5.3; Distribution Loop → v2.5.4; Formal Spec → v2.5.5; PRDs renamed; [prd-v2.5.2-security-follow-up.md](./prd/prd-v2.5.2-security-follow-up.md) created. |
| 2026-07-08 | **v2.5.2 ship**: tag [`v2.5.2`](https://github.com/asap-protocol/asap-protocol/releases/tag/v2.5.2); PyPI **2.5.2**; [#209](https://github.com/asap-protocol/asap-protocol/issues/209) closed; status roll-up flipped to Released. |
| 2026-07-18 | **Pre–v2.5.4 kickoff sync**: roll-up v2.5.3 → Released; v2.5.4 Ready + tasks; sources of truth → Dist Loop; Dist→Spec→Economy handoff documented in PRDs |
| 2026-07-18 | **S5 prep**: `pyproject.toml` / `__version__` → **2.5.4**; CHANGELOG + migration `#upgrading-from-v253-to-v254`; version strings updated (pending tag/PyPI) |
| 2026-09-11 | **Library 2.5.5 ship**: security/quality patch (tag [`v2.5.5`](https://github.com/asap-protocol/asap-protocol/releases/tag/v2.5.5); [#351](https://github.com/asap-protocol/asap-protocol/pull/351)); Formal Spec stays next under historical PRD filename |
| 2026-07-18 | **v2.5.4 ship**: tag [`v2.5.4`](https://github.com/asap-protocol/asap-protocol/releases/tag/v2.5.4); PyPI **2.5.4**; [#294](https://github.com/asap-protocol/asap-protocol/pull/294); post-publish swap; next → Spec |
| 2026-07-11 | **v2.5.3 task plan**: [tasks-v2.5.3-roadmap.md](../engineering/tasks/v2.5.3/tasks-v2.5.3-roadmap.md) + PRD READY FOR KICKOFF (D1–D6). |
