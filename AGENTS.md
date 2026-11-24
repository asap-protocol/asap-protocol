# AGENTS.md

> **Context Map for AI Agents**. Use this file to locate project knowledge.
> **Rules Enforcement**: Strictly follow all active `.cursor/rules/*.mdc`.
> **Agent index**: [`.cursor/README.md`](.cursor/README.md) — precedence, canonical commands, rules vs skills.

## Project Context

**ASAP Protocol** (Async Simple Agent Protocol) is a production-ready protocol for agent-to-agent communication, and the capability, approval, and compliance layer for agents. It interoperates with A2A v1.0 and MCP authorization (OAuth 2.1); it does not replace them.
- **Stack**: Python 3.13+, FastAPI, Pydantic v2.
- **Transport**: JSON-RPC 2.0 over HTTP/WebSocket.
- **Status**: Library **2.5.12** is current (tag [`v2.5.12`](https://github.com/asap-protocol/asap-protocol/releases/tag/v2.5.12), 2026-11-24). **Current versions:** `pyproject.toml` **2.5.12** · PyPI `asap-protocol` **2.5.12** · PyPI `asap-compliance` **1.3.0** (tag [`v2.5.0.1`](https://github.com/asap-protocol/asap-protocol/releases/tag/v2.5.0.1)) · npm `@asap-protocol/client` **2.4.1**. The **v2.5.x train is closed** at 2.5.11; 2.5.12 is a maintenance patch. **Next train:** **v2.6.0 Interop** ([prd-interop.md](product/prd/prd-interop.md)), then **v2.7.0 Spec profile** ([prd-spec-profile.md](product/prd/prd-spec-profile.md)). Train index: [prd-roadmap.md](product/prd/prd-roadmap.md). Release history: [`CHANGELOG.md`](CHANGELOG.md) + GitHub Releases. Release rules: [release-policy.md](docs/maintainers/release-policy.md).
- **Framework Integrations**: LangChain, CrewAI, PydanticAI, LlamaIndex, SmolAgents, Vercel AI SDK, MCP (envelope + **MCP Auth Bridge** v2.5.0), OpenClaw, A2H.
- **npm (TypeScript)**: The official client is **`@asap-protocol/client`** (scoped, **public** on npm for v2.4.x). Maintainer workflow: `.github/workflows/publish-typescript.yml`; context: `docs/maintainers/npm-publishing.md`. Org cutover (Trusted Publisher / Pages / secrets): `docs/maintainers/github-organization.md`.
- **General contact** (humans coordinating on the protocol; not security): [info@asap-protocol.com](mailto:info@asap-protocol.com) — vulnerabilities: [SECURITY.md](SECURITY.md).

## Quick Start

```bash
uv sync                                     # Install dependencies
# Tests: .cursor/README.md#canonical-commands (fast loop, then local floor)
uv run uvicorn asap.transport.server:app --reload  # Start dev server
uv run mypy src/ scripts/ tests/ && uv run ruff check src/  # Verify quality
```

For coverage and pre-push gates, see [`.cursor/README.md`](.cursor/README.md#canonical-commands).

## Agent Guidance (rules vs skills)

| I need to… | Read |
|------------|------|
| Run tests or check coverage | [`.cursor/README.md`](.cursor/README.md#canonical-commands) + `testing-standards.mdc` |
| Test rate-limited endpoints / fix 429 flakes | `testing-rate-limiting.mdc` → `skills/testing-rate-limiting/SKILL.md` |
| Write or refactor code | `agent-clean-code.mdc` |
| Choose where new code lives | `architecture-principles.mdc` |
| Commit or push | `git-commits.mdc` (always on) |
| Cut a release or patch | [`docs/maintainers/release-policy.md`](docs/maintainers/release-policy.md) (exploitable advisory: [`docs/maintainers/hotfix.md`](docs/maintainers/hotfix.md)) |
| Frontend (`apps/web/`) | `frontend-best-practices.mdc` |
| Security audit | `skills/security-review/SKILL.md` |
| Security PR review (high-confidence) | Optional local command under `.cursor/commands/` when present (gitignored) |
| Code quality review | `skills/code-quality-review/SKILL.md` |

**Precedence:** user rules → always-on rules → scoped/requestable rules → skills → commands → docs. Details in [`.cursor/README.md`](.cursor/README.md).

## Knowledge Map

### 1. Product & Architecture (Read First)
- **Vision & Roadmap**: Public train index [`product/prd/prd-roadmap.md`](product/prd/prd-roadmap.md); ADRs and PRDs (`product/decision-records/`, `product/prd/`). Narrative vision files under `product/strategy/` are **local-only** (ignored on the remote); when present, start at `product/strategy/roadmap.md`.
- **Feature Specs (PRDs)**: `product/prd/`
- **Arch Decisions (ADRs)**: `product/decision-records/`
- **Documentation checkpoints** (post-release PRD follow-up): `product/checkpoints.md`
- **Tech Stack**: `engineering/architecture/tech-stack-decisions.md`
- **MCP Auth Bridge**: `asap.mcp.auth` (`protect_server`, `MCPAuthConfig`; legacy shim `asap.adapters.mcp` is removed in v2.6.0) — [docs/adapters/mcp-auth-bridge.md](docs/adapters/mcp-auth-bridge.md)

### 2. Development Status
- **Active Sprint**: none — **Phase 0 reset** (no active feature sprint). The `engineering/tasks/v2.6.0/` folder is created at v2.6.0 kickoff. Last shipped sprint: [engineering/tasks/v2.5.4/](engineering/tasks/v2.5.4/tasks-v2.5.4-roadmap.md) (Distribution Loop).
- **Roadmap**: v2.5.x closed at **2.5.11** (2.5.5–2.5.12 are maintenance patches) → **v2.6.0 Interop** (A2A v1.0 Agent Card bridge, external agent tokens incl. MCP OAuth 2.1, `@asap-protocol/mcp-auth` for HTTP MCP servers) → **v2.7.0 Spec profile** → decision gate. Economy stays a trigger-gated product track, not library v3.0.0. Train index: [prd-roadmap.md](product/prd/prd-roadmap.md); release history: [CHANGELOG.md](CHANGELOG.md).
- **Release policy & support tiers**: [docs/maintainers/release-policy.md](docs/maintainers/release-policy.md) (planned scope ships only in x.Y.0; patches are unplanned maintenance) · [docs/integrations/support-tiers.md](docs/integrations/support-tiers.md).
- **Code Reviews**: `engineering/code-review/`

## Organization

### Project Structure
```text
src/asap/
├── adapters/      # Third-party wire adapters (OpenAPI, MCP auth bridge)
├── models/        # Data models (Envelope, TaskRequest, TaskStream)
├── auth/          # OAuth2/OIDC, Agent Identity, Capabilities, Approval
├── transport/     # HTTP Client/Server, WebSocket, SSE Streaming
├── state/         # Async persistence (AsyncSnapshotStore/AsyncMeteringStore)
├── handlers/      # Task processing logic
├── economics/     # Metering, Delegation, SLA
└── discovery/     # Manifests, Health, Lite Registry
```

### AI Toolbox (Available Capabilities)
- **Agent index**: [`.cursor/README.md`](.cursor/README.md) — start here for rules, skills, and commands
- **Rules**: `.cursor/rules/*.mdc` (auto-loaded or requestable by glob)
- **Commands**: `.cursor/commands/` — optional local slash workflows (**gitignored**; not required in-repo), same class as `product/strategy/`
- **Skills**: `.cursor/skills/` (Security review, code quality, rate-limit testing)
- **Web E2E**: `apps/web/docs/playwright-e2e.md` — Playwright browser path troubleshooting

## Key Architectural Patterns

1.  **Envelope Protocol**: All messages wrapped in `Envelope[T]` (`models/envelope.py`).
2.  **State Machine**: Tasks strictly follow `submitted → working → completed` (`models/enums.py`).
3.  **Circuit Breaker**: Transport reliability logic (`transport/http_client.py`).
4.  **Agent Identity**: Per-runtime Ed25519 identity with Host/Agent JWT (`auth/agent_jwt.py`).
5.  **Capability AuthZ**: Constraint-based capability grants (`auth/capabilities.py`).

## Security Context

- **Auth**: OAuth2/OIDC for agent-to-agent (ADR-17); Agent Identity with Host/Agent JWT (v2.2).
- **Identity**: Ed25519 Signed Manifests (v1.2); Per-runtime agent identity with JWK thumbprint (v2.2).
- **Capabilities**: Constraint-based authorization with approval flows (v2.2).
- **Transport**: mTLS optional (v1.2); ASAP-Version negotiation (v2.2).
- **Compliance**: `asap-compliance` package validates specs.
