# Issue #275: Remove `asap.cli._compat` (v2.6.0)

**Issue**: [#275](https://github.com/adriannoes/asap-protocol/issues/275)
**Target release**: v2.6.0
**Depends on**: #242 shipped ([#274](https://github.com/adriannoes/asap-protocol/pull/274))
**Scheduled in**: [`product/prd/prd-interop.md`](../../../product/prd/prd-interop.md) housekeeping — HK-001 (v2.6.0 Interop, added 2026-11-18)

## Context

The v2.5.1 S3 CLI split moved legacy re-exports into `asap.cli._compat` to keep
`cli/__init__.py` ≤80 LOC. The shim is a **deprecation bridge**, not a permanent
API — same pattern as `asap.transport.websocket` and `asap.adapters.mcp`.

## Relevant Files

### Remove
- `src/asap/cli/_compat.py`

### Modify
- `tests/test_cli.py` — import from canonical modules
- `tests/cli/test_compat.py` — remove shim contract tests; keep LOC ceiling if needed
- `docs/migration.md` — breaking change note for v2.6.0
- `CHANGELOG.md` — `[2.6.0]` breaking removal entry

## Tasks

### 1.0 Repoint consumers

- [ ] 1.1 Grep `asap.cli._compat` — zero in-repo hits after change
- [ ] 1.2 Update `tests/test_cli.py` to canonical imports

### 2.0 Remove shim

- [ ] 2.1 Delete `src/asap/cli/_compat.py`
- [ ] 2.2 Adjust `tests/cli/test_compat.py` (drop shim tests; retain `__init__.py` LOC guard if desired)

### 3.0 Docs & release

- [ ] 3.1 `CHANGELOG.md` `[2.6.0]` breaking note
- [ ] 3.2 `docs/migration.md` — v2.6.0 upgrade section

### 4.0 Verification

- [ ] `uv run pytest tests/test_cli.py tests/test_cli_edge_cases.py tests/cli/`
- [ ] `uv run ruff check src/asap/cli/` + `uv run mypy src/asap/cli/`

## Acceptance

See issue #275 checklist.
