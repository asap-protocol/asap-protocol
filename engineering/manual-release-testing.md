# Manual Release Testing Checklist (v2.2.0)

Simulates a real user validating the ASAP Protocol before release (aligned with **Protocol Hardening**: identity, capabilities, streaming/SSE, batch JSON-RPC, versioning, errors, async stores, audit, Compliance Harness v2).

**Last run**: 2026-04-16 — Sections **7–8** (Compliance Harness v2 + smoke v2.2): **105 passed**; `asap.__version__` **2.2.0** ✅  

| Step | Scope | Result |
|------|--------|--------|
| §7 | `tests/testing/test_compliance_v2.py` | 11 passed |
| §8 | `tests/e2e/test_streaming.py` + `tests/transport/test_streaming.py` | 5 passed |
| §8 | `tests/transport/test_batch.py` | 9 passed |
| §8 | `test_server.py` (Agent register/status/revoke/rotate-key classes) | 29 passed |
| §8 | `tests/transport/test_capability_routes.py` | 24 passed |
| §8 | `tests/economics/test_audit.py` | 27 passed |

**Previous release baseline**: v1.3.0 checklist below was archived in git history; this file tracks **v2.2.0** going forward.

## 1. Installation & Environment

- [ ] `uv sync` succeeds
- [ ] `asap --version` shows **2.2.0** (or `uv run python -c "import asap; print(asap.__version__)"`)
- [ ] Python 3.13+ detected

## 2. CLI Commands

- [ ] `asap --help` works
- [ ] `asap list-schemas` lists schemas
- [ ] `asap keys generate -o /tmp/test-key.pem` creates Ed25519 keypair
- [ ] `asap manifest sign` / `verify` / `info` work with sample manifest

## 3. Quick Demo

- [ ] `uv run python -m asap.examples.run_demo` completes successfully (echo agent + coordinator)

## 4. Examples (smoke)

- [ ] `uv run python -m asap.examples.echo_agent` starts (Ctrl+C to stop)
- [ ] `run_demo` covers coordinator flow
- [ ] Orchestration example available (if still documented)

## 5. Server + Client Flow

- [ ] Start echo agent in background
- [ ] Send task via `ASAPClient` (or coordinator)
- [ ] Receive valid `TaskResponse`

## 6. External Compliance Package (`asap-compliance`)

- [ ] `asap-compliance` present under workspace root
- [ ] `cd asap-compliance && uv run pytest` (or project-documented path) — all pass
- [ ] Optional live handshake against a running agent: `validate_handshake(ComplianceConfig(agent_url="http://127.0.0.1:8001"))` — **PASS**

## 7. Compliance Harness v2 (in-repo)

Runs automated checks for streaming, errors, versioning, batch, audit, identity hooks (see `asap.testing.compliance`).

- [ ] `uv run pytest tests/testing/test_compliance_v2.py -q` — all pass

## 8. v2.2 Protocol Hardening — Focused pytest (optional but recommended)

Repeat the same scenarios covered by CI without running the full suite:

- [ ] Streaming / SSE + client: `uv run pytest tests/e2e/test_streaming.py tests/transport/test_streaming.py -q`
- [ ] JSON-RPC batch + `ASAPClient.batch`: `uv run pytest tests/transport/test_batch.py -q`
- [ ] Identity routes (register / status / revoke / rotate-key):  
  `uv run pytest tests/transport/test_server.py -k "TestAgentRegisterEndpoint or TestAgentStatusEndpoint or TestAgentRevokeEndpoint or TestAgentRotateKeyEndpoint" -q`
- [ ] Capability routes: `uv run pytest tests/transport/test_capability_routes.py -q`
- [ ] Audit chain: `uv run pytest tests/economics/test_audit.py -q`

## 9. Full Test Suite

- [ ] Fast loop in [Canonical commands](../.cursor/README.md#canonical-commands) — expect **all passed**, a small number **skipped** (rate-limit isolation / optional markers); update counts below when you run.

**Reference run (local, 2026-04-16)**: `2953 passed`, `7 skipped` in ~130s without `-n auto`.

- [ ] Local floor in [Canonical commands](../.cursor/README.md#canonical-commands). CI applies the floor with `uv run coverage report --fail-under=85`.

## 10. Lint & Type Check

- [ ] `uv run ruff check .` — All checks passed
- [ ] `uv run ruff format --check .`
- [ ] `uv run mypy src/ scripts/ tests/` — Success

## 11. Security audit (same graph as CI)

- [ ] `uv sync --frozen --all-extras --dev --no-extra crewai --no-extra llamaindex && uv run pip-audit --ignore-vuln CVE-2026-4539 --ignore-vuln CVE-2026-4963 --ignore-vuln CVE-2026-2654 --ignore-vuln PYSEC-2024-271 --ignore-vuln PYSEC-2026-89 --ignore-vuln PYSEC-2025-183` — matches CI (see `SECURITY.md` if flags change)

---

### How this relates to automated release verification

The sprint **S6** checklist (`engineering/tasks/v2.2.0/sprint-S6-release.md`) lists the full CI commands before tag/PyPI. **This file** is the human walkthrough: repeat sections **7–10** to mirror what we validated before v2.2.0 ship; use section **8** for a faster smoke that still touches Protocol Hardening surfaces.
