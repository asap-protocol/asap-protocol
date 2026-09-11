# AGENTS.md

> **Context Map for AI Agents**. Use this file to locate project knowledge.
> **Rules Enforcement**: Strictly follow all active `.cursor/rules/*.mdc`.
> **Agent index**: [`.cursor/README.md`](.cursor/README.md) — precedence, canonical commands, rules vs skills.

## Project Context

**ASAP Protocol** (Async Simple Agent Protocol) is a production-ready standard for agent-to-agent communication.
- **Stack**: Python 3.13+, FastAPI, Pydantic v2.
- **Transport**: JSON-RPC 2.0 over HTTP/WebSocket.
- **Status**: v2.5.5 **shipped** (security & quality patch, tag [`v2.5.5`](https://github.com/asap-protocol/asap-protocol/releases/tag/v2.5.5), 2026-09-11). **Current versions:** `pyproject.toml` **2.5.5** · PyPI `asap-protocol` **2.5.5** · PyPI `asap-compliance` **1.3.0** (tag [`v2.5.0.1`](https://github.com/asap-protocol/asap-protocol/releases/tag/v2.5.0.1)) · npm `@asap-protocol/client` **2.4.1**. This patch is **not** Formal Spec. **Predecessor:** Distribution Loop **v2.5.4** shipped (tag [`v2.5.4`](https://github.com/asap-protocol/asap-protocol/releases/tag/v2.5.4), 2026-07-18). **Next product train:** Formal Spec ([prd-v2.5.5-formal-spec-interop.md](product/prd/prd-v2.5.5-formal-spec-interop.md)). See `CHANGELOG.md` `[2.5.5]` and `docs/migration.md#upgrading-from-v254-to-v255`.
- **Framework Integrations**: LangChain, CrewAI, PydanticAI, LlamaIndex, SmolAgents, Vercel AI SDK, MCP (envelope + **MCP Auth Bridge** v2.5.0), OpenClaw, A2H.
- **npm (TypeScript)**: The official client is **`@asap-protocol/client`** (scoped, **public** on npm for v2.4.x). Maintainer workflow: `.github/workflows/publish-typescript.yml`; context: `docs/maintainers/npm-publishing.md`. Org cutover (Trusted Publisher / Pages / secrets): `docs/maintainers/github-organization.md`.
- **General contact** (humans coordinating on the protocol; not security): [info@asap-protocol.com](mailto:info@asap-protocol.com) — vulnerabilities: [SECURITY.md](SECURITY.md).

## Quick Start

```bash
uv sync                                     # Install dependencies
uv run pytest -n auto --tb=short            # Fast test run (CI-parity)
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
| Frontend (`apps/web/`) | `frontend-best-practices.mdc` |
| Security audit | `skills/security-review/SKILL.md` |
| Security PR review (high-confidence) | Optional local command under `.cursor/commands/` when present (gitignored) |
| Code quality review | `skills/code-quality-review/SKILL.md` |

**Precedence:** user rules → always-on rules → scoped/requestable rules → skills → commands → docs. Details in [`.cursor/README.md`](.cursor/README.md).

## Knowledge Map

### 1. Product & Architecture (Read First)
- **Vision & Roadmap**: ADRs and PRDs (`product/decision-records/`, `product/prd/`). Narrative vision files under `product/strategy/` are **local-only** (ignored on the remote); when present, start at `product/strategy/roadmap.md`.
- **Feature Specs (PRDs)**: `product/prd/`
- **Arch Decisions (ADRs)**: `product/decision-records/`
- **Documentation checkpoints** (post-release PRD follow-up): `product/checkpoints.md`
- **Tech Stack**: `engineering/architecture/tech-stack-decisions.md`
- **MCP Auth Bridge**: `asap.adapters.mcp` (`protect_server`, `MCPAuthConfig`) — [docs/adapters/mcp-auth-bridge.md](docs/adapters/mcp-auth-bridge.md)

### 2. Development Status
- **Active Sprint**: [engineering/tasks/v2.5.4/](engineering/tasks/v2.5.4/tasks-v2.5.4-roadmap.md) — Distribution Loop (**SHIPPED**); library **2.5.5** is a security/quality patch on `main`
- **Adoption Roadmap**: Library patch **v2.5.5** shipped 2026-09-11 (tag `v2.5.5`); Distribution Loop (**v2.5.4** shipped 2026-07-18, tag `v2.5.4`; [PRD](product/prd/prd-v2.5.4-distribution-loop.md), [tasks](engineering/tasks/v2.5.4/tasks-v2.5.4-roadmap.md)); v2.5.3 **Adapter Lab II** shipped (2026-07-16, tag `v2.5.3`); v2.5.2 **security follow-up** shipped (2026-07-08, tag `v2.5.2`); v2.5.1 **code quality patch** shipped (2026-06-26); v2.5.0 **MCP Auth Bridge** shipped (2026-06-24); `asap-compliance` **1.3.0** on PyPI (tag `v2.5.0.1`); **next product train:** Formal Spec ([prd-v2.5.5-formal-spec-interop.md](product/prd/prd-v2.5.5-formal-spec-interop.md)); `@asap-protocol/mcp-auth` (HTTP/SSE) still deferred.
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
