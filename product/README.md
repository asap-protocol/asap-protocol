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

- **`strategy/`** — Long-form vision, living `strategy/roadmap.md` hub, literature notes (optional local checkout; not on GitHub).
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
| v2.5.5 and later v2.5.x patches | See [GitHub Releases](https://github.com/asap-protocol/asap-protocol/releases/latest) | `CHANGELOG.md` | Security fixes, dependency floors, and regression tests. The v2.5.x feature train closed at 2.5.11. |
| **Phase 0 reset** | Checkpoint from 2026-11-18 | [Roadmap](./prd/prd-roadmap.md) | The reset closed the v2.5.x feature train and introduced the release policy and support tiers. See [CP-8](./checkpoints.md#cp-8-roadmap-reset-2026-11-18) for follow-up work. |
| **v2.6.0 Interop** | **Current source version** | [Interop PRD](./prd/prd-interop.md) | Signed A2A v1.0 Agent Cards, grants for external OAuth 2.1 access tokens, and `@asap-protocol/mcp-auth` for HTTP MCP servers. See the [interoperability guide](../docs/guides/interoperability.md). |
| **v2.7.0 Spec profile** | **Next** | [Spec profile PRD](./prd/prd-spec-profile.md) | RFC 2119 profile for ASAP identity, capability grants, approvals, lifecycle, and errors. Each MUST will map to an `asap-compliance` check. |
| Economy (product track) | 🔭 Long-term (triggers) | `prd-v3.0-economy.md` | Settlement, billing; trigger-gated and not tied to library v3.0.0; ADR-21 free badge until launch |

The Spec profile follows Interop so it can describe how ASAP works with A2A and MCP. After v2.7.0, a decision gate reviews adoption data and sets the v3 direction. The [release policy](../docs/maintainers/release-policy.md) limits planned scope to minor releases.

Registry API Backend (PostgreSQL), Intent-Based Search, Orchestration Primitives, Delegated/Autonomous Mode formalization, and DeepEval remain deferred until their triggers are met. See the [public roadmap](./prd/prd-roadmap.md) and the local `strategy/roadmap.md` when present.

## Key entrypoints

- **[Roadmap](./prd/prd-roadmap.md)** — Living public train index (current version, next trains).
- **[PRD index](./prd/README.md)** — All versioned requirements.
- **[Tasks index](../engineering/tasks/README.md)** — Sprint folders per release.
- **[Decision index](./decision-records/README.md)** — ADR catalog.
- **[Documentation checkpoints](./checkpoints.md)** — Post-release PRD refresh schedule.

> **Visibility policy**: Public docs should explain the stable protocol, released capabilities, adoption guides, and high-level roadmap. Detailed go-to-market, pricing, private feature timing, fundraising, and near-term tactical PRDs live in ignored private folders.
