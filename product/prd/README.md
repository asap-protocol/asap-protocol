# Product Requirements Documents (`product/prd/`)

Index of PRDs. **Shipped** releases have retrospective PRDs. **Planned** work is indexed in [prd-roadmap.md](./prd-roadmap.md). New PRDs are named by track, not by version; the target version is in each PRD header.

**Current library:** `asap-protocol` **2.5.11** · **Next train:** Interop (v2.6.0) · **Release history:** [`CHANGELOG.md`](../../CHANGELOG.md)

> **Deprecated redirect:** [prd-v2.4-adoption.md](./prd-v2.4-adoption.md) — do not use for new tasks.

---

## Active / next

| Track | File | Status |
|-------|------|--------|
| **Roadmap** (train index) | [prd-roadmap.md](./prd-roadmap.md) | Index — living |
| **Interop** — v2.6.0 | [prd-interop.md](./prd-interop.md) | **Next** — PLANNED |
| **Spec profile** — v2.7.0 | [prd-spec-profile.md](./prd-spec-profile.md) | PLANNED (after Interop) |
| **Economy** — product track | [prd-v3.0-economy.md](./prd-v3.0-economy.md) | Vision draft (trigger-gated; not library v3.0.0) |

---

## Shipped (v2.x)

| Version | File | Shipped |
|---------|------|---------|
| v2.5.5–v2.5.11 | *(maintenance patches — security fixes, dependency floors, regression tests; no PRD)* — see [`CHANGELOG.md`](../../CHANGELOG.md) | 2026-09-11 → 2026-11-09 — [GitHub Releases](https://github.com/asap-protocol/asap-protocol/releases) |
| v2.5.4 | [prd-v2.5.4-distribution-loop.md](./prd-v2.5.4-distribution-loop.md) · [tasks](../../engineering/tasks/v2.5.4/tasks-v2.5.4-roadmap.md) | 2026-07-18 — tag [`v2.5.4`](https://github.com/asap-protocol/asap-protocol/releases/tag/v2.5.4) |
| v2.5.3 | [prd-v2.5.3-adapter-lab-ii.md](./prd-v2.5.3-adapter-lab-ii.md) · [tasks](../../engineering/tasks/v2.5.3/tasks-v2.5.3-roadmap.md) | 2026-07-16 — tag [`v2.5.3`](https://github.com/asap-protocol/asap-protocol/releases/tag/v2.5.3) |
| v2.5.2 | [prd-v2.5.2-security-follow-up.md](./prd-v2.5.2-security-follow-up.md) | 2026-07-08 |
| v2.5.1 | *(code quality patch — no public PRD; see `engineering/tasks/private/v2.5.1/`)* | 2026-06-26 |
| v2.5.0 | [prd-v2.5.0-mcp-auth-bridge.md](./prd-v2.5.0-mcp-auth-bridge.md) | 2026-06-24 |
| v2.4.1 | [prd-v2.4.1-security-hardening.md](./prd-v2.4.1-security-hardening.md) | 2026-06-14 |
| v2.4.0 | [prd-v2.4.0-edge-ai-discovery.md](./prd-v2.4.0-edge-ai-discovery.md) | 2026-05-24 |
| v2.3.0 | [prd-v2.3-scale.md](./prd-v2.3-scale.md) | 2026-05-04 |
| v2.3.1 | `private/prd-v2.3.1-adapter-lab.md` | 2026-05-21 |
| v2.2.1 | [prd-v2.2.1-patch.md](./prd-v2.2.1-patch.md) | 2026-04-21 |
| v2.2.0 | [prd-v2.2-protocol-hardening.md](./prd-v2.2-protocol-hardening.md) | 2026-04-15 |
| v2.1.0 | [prd-v2.1-ecosystem.md](./prd-v2.1-ecosystem.md) | 2026-02-28 |
| v2.0.0 | [prd-v2.0-roadmap.md](./prd-v2.0-roadmap.md) | 2026-02-23 |

---

## Historical / superseded

| File | Notes |
|------|-------|
| [prd-v2.2-scale.md](./prd-v2.2-scale.md) | Superseded by `prd-v2.2-protocol-hardening.md` |
| [prd-v2.4-adoption.md](./prd-v2.4-adoption.md) | Deprecated — split into v2.4.0/2.4.1 shipped + v2.5.x |
| `private/prd-v2.3.2-*` | Renumbered → `prd-v2.5.3-adapter-lab-ii.md` (was v2.5.1) |
| `private/prd-v2.3.3-*` | Renumbered → `prd-v2.5.4-distribution-loop.md` (was v2.5.2) |
| `prd-v2.5.1-adapter-lab-ii.md` | Renamed → `prd-v2.5.3-adapter-lab-ii.md` (2026-07-08) |
| `prd-v2.5.2-distribution-loop.md` | Renamed → `prd-v2.5.4-distribution-loop.md` (2026-07-08) |
| `prd-v2.5.3-formal-spec-interop.md` | Renamed → `prd-v2.5.5-formal-spec-interop.md` (2026-07-08) |
| [prd-v2.5-roadmap.md](./prd-v2.5-roadmap.md) | v2.5.x train index — **closed** 2026-11-18 at 2.5.11; superseded by [prd-roadmap.md](./prd-roadmap.md) |
| [prd-v2.5.5-formal-spec-interop.md](./prd-v2.5.5-formal-spec-interop.md) | Redirect stub — split 2026-11-18 into [prd-interop.md](./prd-interop.md) (v2.6.0) + [prd-spec-profile.md](./prd-spec-profile.md) (v2.7.0) |

---

## Cross-cutting PRDs

| File | Scope |
|------|-------|
| [prd-a2h-integration.md](./prd-a2h-integration.md) | A2H (shipped v2.2) |
| [prd-asap-implementation.md](./prd-asap-implementation.md) | v0.1 foundation |
| [prd-cross-platform-integration-asap.md](./prd-cross-platform-integration-asap.md) | Web app |
| [prd-cross-platform-integration-agentic.md](./prd-cross-platform-integration-agentic.md) | External repo |
| [prd-design-system-revamp.md](./prd-design-system-revamp.md) | UI/UX |
| [prd-review-schedule.md](./prd-review-schedule.md) | Legacy process (v0.5–v1.0) |

---

## v1.x archive

`prd-v1-roadmap.md`, `prd-v1.1-roadmap.md`, `prd-v1.2-roadmap.md`, `prd-v1.3-roadmap.md`, `prd-v1.4-roadmap.md`

---

## Task folders

See [engineering/tasks/README.md](../../engineering/tasks/README.md).
