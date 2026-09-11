# Product (`product/`)

This folder holds **product-level** documentation for the ASAP Protocol: PRDs, architecture decision records (ADRs), and design guides.

## What lives here

| Area | Role |
|------|------|
| **`prd/`** | Product Requirements Documents — versioned specs and shipped scope. |
| **`decision-records/`** | ADRs (indexed via `README.md` and `ADR-INDEX.md`). |
| **`design/`** | UI/UX source of truth (`design-system.md`, mobile strategy, references). |
| **`checkpoints.md`** | Post-release documentation review schedule (PRD updates, velocity, retros). |

## Local-only / ignored paths

Per `.gitignore`, the following are **not committed** to the remote (keep them locally if you use them):

- **`strategy/`** — Long-form vision, living [roadmap.md](./strategy/roadmap.md) hub, literature notes (optional local checkout).
- **`private/`**, **`private-roadmap/`**, **`prd/private/`**, and selective **`prd/prd-v2.3.[1-9]-*.md`** — Commercial or tactical drafts.

Public READMEs and docs should prefer **`decision-records/`**, **`prd/`** (public filenames), and **`docs/`** for links that must work on GitHub.

## Directory layout (overview)

```
product/
├── README.md                 # This file
├── checkpoints.md          # Post-release doc review milestones (follow-up)
├── strategy/                 # Local-only when ignored (vision, living roadmap hub)
├── decision-records/         # ADRs
├── design/                   # Design system & UX references
└── prd/                      # PRDs (including private/ when present locally)
```

### Current roadmap status

| Version | Status | PRD | Focus |
|---|---|---|---|
| v2.1.0 / v2.1.1 | ✅ Released | `prd-v2.1-ecosystem.md` | Consumer SDK + ecosystem |
| **v2.2.0** | **✅ Released (2026-04-15)** | `prd-v2.2-protocol-hardening.md` | Identity, capabilities, streaming, batch, audit |
| **v2.2.1** | **✅ Released (2026-04-21)** | `prd-v2.2.1-patch.md` | WebAuthn real, CLI compliance/audit |
| v2.3.0 — Adoption Core | ✅ Released (2026-05-04) | `prd-v2.3-scale.md` | OpenAPI Adapter, TypeScript SDK, Auto-Registration, escalation, ASAP challenge |
| v2.3.1 — Framework adapters (npm) | ✅ Released (2026-05-21) | `private/prd-v2.3.1-adapter-lab.md` | `@asap-protocol/mastra`, `@asap-protocol/openai-agents` @ 2.3.1; Python core stays 2.3.0 |
| **v2.4.0 — Edge-AI discovery** | **✅ Released (2026-05-24)** | `prd-v2.4.0-edge-ai-discovery.md` | Hardware/inference manifest fields, registry mirror, marketplace filters, ShellClaw onboarding |
| **v2.4.1 — Security hardening** | **✅ Released (2026-06-14)** | `prd-v2.4.1-security-hardening.md` | OAuth2 iss/aud validation, fail-closed identity binding, web SSRF/redirect hardening |
| **v2.5.0 — MCP Auth Bridge** | **✅ Released (2026-06-24)** | `prd-v2.5.0-mcp-auth-bridge.md` + [tasks-v2.5.0-roadmap.md](../engineering/tasks/v2.5.0/tasks-v2.5.0-roadmap.md) | `asap.adapters.mcp`, compliance `mcp-auth-bridge`; tag [`v2.5.0`](https://github.com/asap-protocol/asap-protocol/releases/tag/v2.5.0) |
| **v2.5.1 — Code quality patch** | **✅ Released (2026-06-26)** | `engineering/tasks/private/v2.5.1/` | Code-quality audit S0–S3 + P0 fixes; Adapter Lab II slipped |
| **v2.5.2 — Security follow-up** | **✅ Released (2026-07-08)** | `prd-v2.5.2-security-follow-up.md` | #209 + CR #245–#249 + registry fixes (ex planned v2.5.4); tag [`v2.5.2`](https://github.com/asap-protocol/asap-protocol/releases/tag/v2.5.2) |
| **v2.5.3 — Adapter Lab II** | **✅ Released (2026-07-16)** | `prd-v2.5.3-adapter-lab-ii.md` + [tasks](../engineering/tasks/v2.5.3/tasks-v2.5.3-roadmap.md) | Workflow connectors, automation security, experimental MAF / NAT; tag [`v2.5.3`](https://github.com/asap-protocol/asap-protocol/releases/tag/v2.5.3) |
| **v2.5.4 — Distribution Loop** | **✅ Released (2026-07-18)** | `prd-v2.5.4-distribution-loop.md` + [tasks](../engineering/tasks/v2.5.4/tasks-v2.5.4-roadmap.md) | Homepage, starters, Build for agents, telemetry ops; tag [`v2.5.4`](https://github.com/asap-protocol/asap-protocol/releases/tag/v2.5.4) |
| **v2.5.5 — Security & quality patch** | **✅ Released (2026-09-11)** | `CHANGELOG.md` `[2.5.5]` (no Formal Spec PRD) | OpenAPI climb, registry URN guard, marketplace SSRF pin, capability-replace consent, fail-closed reactivate; tag [`v2.5.5`](https://github.com/asap-protocol/asap-protocol/releases/tag/v2.5.5) |
| Formal Spec & Interop | 🔭 **Next** (kickoff) | `prd-v2.5.5-formal-spec-interop.md` | RFC spec, introspection, privacy, cross-protocol (library **2.5.5** is the patch above, not this train) |
| v2.5.x train index | Active | `prd-v2.5-roadmap.md` | Full schedule + orphan owners + rescope log |
| v3.0 — Economy | 🔭 Long-term (triggers) | `prd-v3.0-economy.md` | Settlement, billing; ADR-21 free badge until launch |

> **Strategic note**: Registry API Backend (PostgreSQL), Intent-Based Search, Orchestration Primitives, Delegated/Autonomous Mode formalization, and DeepEval remain deferred until their triggers materialize. **Library v2.5.5** is a security/quality patch. Dist Loop (v2.5.4) remains shipped. Next public train work: **Formal Spec** ([prd-v2.5.5-formal-spec-interop.md](./prd/prd-v2.5.5-formal-spec-interop.md)). Local strategy hub (when present): [strategy/roadmap.md](./strategy/roadmap.md). Public train: [prd/README.md](./prd/README.md).

## Key entrypoints

- **[PRD index](./prd/README.md)** — All versioned requirements.
- **[Tasks index](../engineering/tasks/README.md)** — Sprint folders per release.
- **[Decision index](./decision-records/README.md)** — ADR catalog.
- **[Documentation checkpoints](./checkpoints.md)** — Post-release PRD refresh schedule.

> **Visibility policy**: Public docs should explain the stable protocol, released capabilities, adoption guides, and high-level roadmap. Detailed go-to-market, pricing, private feature timing, fundraising, and near-term tactical PRDs live in ignored private folders.
