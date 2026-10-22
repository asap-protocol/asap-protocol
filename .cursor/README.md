# Cursor Agent Guide

Index for AI agents working in the ASAP Protocol repository.

## Precedence

When instructions conflict, follow this order:

1. **User rules** (Cursor settings for this workspace)
2. **Always-on rules** (`alwaysApply: true` in `.cursor/rules/`)
3. **Scoped / requestable rules** (loaded by glob or on demand)
4. **Skills** (`.cursor/skills/*/SKILL.md` — procedural workflows)
5. **Commands** (optional `.cursor/commands/` — slash workflows with fixed output; **gitignored / local-only**, not required in-repo)
6. **Docs** (`docs/`, `AGENTS.md`, `CONTRIBUTING.md`)

## Rules vs skills vs commands

| Kind | Location | Purpose |
|------|----------|---------|
| **Rules** | `.cursor/rules/*.mdc` | Constraints and standards (auto-loaded or requestable) |
| **Skills** | `.cursor/skills/*/SKILL.md` | Deep procedural knowledge for a specific task |
| **Commands** | `.cursor/commands/*.md` (optional) | Local slash-command workflows; **not committed** — keep locally if you use them |

**Bridge pattern:** A thin rule points to a skill when the topic needs detail. Example: `testing-rate-limiting.mdc` → `skills/testing-rate-limiting/SKILL.md`.

## Canonical commands

Match CI (`.github/workflows/ci.yml`). The test job collects coverage with `-n auto` and `--cov` in one run. The coverage job only enforces the 85% floor and uploads the report.

```bash
# Tests and coverage — same suite as CI test (python), with the floor applied locally
uv run pytest -n auto --tb=short --cov=asap --cov-report=xml --cov-fail-under=85

# Pre-push quality gate (see git-commits.mdc for full list)
uv run ruff check .
uv run ruff format --check .
uv run mypy src/ scripts/ tests/
uv run pytest -n auto --tb=short --cov=asap --cov-report=xml --cov-fail-under=85
```

Web app changes (`apps/web/`): also run `npm run lint`, `npx tsc --noEmit`, `npm run test:coverage`, `npm run build`.

## When to read what

| Task | Read first |
|------|------------|
| Any Python code | `python-best-practices.mdc`, `security-standards.mdc` (always on) |
| Any commit / push | `git-commits.mdc` (always on) |
| Clean code / refactor | `agent-clean-code.mdc` |
| Product layout (Core / Web / Registry) | `architecture-principles.mdc` |
| Write or review tests | `testing-standards.mdc` |
| Rate-limited endpoints / flaky 429 | `testing-rate-limiting.mdc` → `skills/testing-rate-limiting/SKILL.md` |
| Frontend (`apps/web/`) | `frontend-best-practices.mdc` |
| Security audit (general) | `skills/security-review/SKILL.md` |
| Security PR review (high-confidence only) | Optional local command under `.cursor/commands/` when present |
| Code quality review | `skills/code-quality-review/SKILL.md` |

## Rules inventory

| File | Layer | `alwaysApply` | Scope |
|------|-------|---------------|-------|
| `security-standards.mdc` | policy | yes | `*.py`, `*.ts`, `*.tsx` |
| `git-commits.mdc` | workflow | yes | global |
| `python-best-practices.mdc` | standards | yes | `**/*.py` |
| `agent-clean-code.mdc` | standards | no | global — SOLID, naming, tests policy |
| `architecture-principles.mdc` | standards | no | global — product architecture only |
| `testing-standards.mdc` | standards | no | `tests/**` |
| `testing-rate-limiting.mdc` | bridge | no | → skill |
| `frontend-best-practices.mdc` | standards | no | `apps/web/**`, `packages/ui/**` |

## Skills inventory

| Skill | Use when |
|-------|----------|
| `testing-rate-limiting` | SlowAPI isolation, HTTP 429 flakes in transport tests |
| `security-review` | OWASP-style audit against project security rules |
| `code-quality-review` | Structure, types, docs, test coverage review |
| `skill-creator` | Scaffold a new skill |
