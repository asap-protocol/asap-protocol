# Security Policy

## Supported Versions

The following versions of the ASAP Protocol specification and reference implementation are currently supported with security updates.

| Version | Supported          |
| ------- | ------------------ |
| >= 2.0.0| :white_check_mark: |
| 1.x.x   | :white_check_mark: |
| < 1.0.0 | :x:                |

The `>= 2.0.0` line includes all current **2.x** reference releases on PyPI (including **2.2.x**). Install the latest patch with `pip install -U asap-protocol`.

## Scope of Coverage

This security policy applies to the **ASAP Protocol** ecosystem. Vulnerabilities in the following components are considered **in-scope**:

- **ASAP Protocol Reference Implementations** (`src/asap/` Python package)
- **Marketplace & Registry API** (`apps/web/` Next.js frontend and IssueOps workflow)
- **Cryptographic implementations** (Ed25519 signatures, RFC 8032/8785 handling)
- **Agent Server/Client Tooling** (FastAPI routers, WebSocket handlers)

**Out of Scope**:
- Vulnerabilities within external agents listed in the `registry.json`. Each agent is maintained by its respective owner and must be reported directly to them.
- Third-party dependencies (unless the vulnerability requires a change in how ASAP interacts with the dependency).

## Reporting a Vulnerability

We take the security of ASAP Protocol seriously. If you have found a security vulnerability, please do NOT report it publicly.

### How to Report

We use GitHub's **Private Vulnerability Reporting**. If you have found a security vulnerability, please report it privately using the "Report a vulnerability" button in the **Security** tab of this repository.

Direct link: [Report a vulnerability](https://github.com/asap-protocol/asap-protocol/security/advisories/new)

If for some reason you cannot use the GitHub reporting tool, please open an issue asking for a private communication channel without disclosing the vulnerability details.

### Information to Include

Please include as much information as possible to help us reproduce and validate the issue:

-   **Description**: A clear description of the vulnerability.
-   **Reproduction Steps**: Detailed steps to reproduce the vulnerability (a Proof of Concept script is highly appreciated).
-   **Environment**:
    -   Library version (e.g., 0.1.0)
    -   Python version (e.g., 3.13)
    -   Operating system
-   **Impact**: How this vulnerability could be exploited and what the potential impact is.
-   **Logs/Output**: Any relevant error messages or logs.

### What Happens Next

1.  **Acknowledgement**: We will acknowledge your report within 48 hours via the GitHub advisory discussion.
2.  **Investigation**: We will investigate the issue and collaborate with you in the private advisory to confirm the vulnerability.
3.  **Resolution**: We will work on a fix. GitHub allows us to create a private fork to develop the fix and collaborate securely.
4.  **Release**: Once the fix is ready and verified, we will merge it, release a new version of the package, and publish a Security Advisory (CVE) crediting you for the discovery (unless you prefer to remain anonymous).

Thank you for helping keep the ASAP Protocol secure!

## Security Update Policy

We use [Dependabot](https://docs.github.com/en/code-security/dependabot) to automatically monitor dependencies for security vulnerabilities and create pull requests with fixes.

### Automatic Security Updates

Dependabot automatically creates pull requests for security updates when vulnerabilities are detected in our dependencies. These updates are independent of our monthly version update schedule and are created immediately upon detection.

### Response Times by Severity

We aim to review and merge security updates according to the following target times. These are goals, not strict commitments, as we are a solo-maintained project:

| Severity | Target Review Time | Action |
|----------|-------------------|--------|
| **Critical** | 3-5 business days | Priority review and merge if CI passes |
| **High** | 1-2 weeks | Priority review, merge after validation |
| **Medium** | 2-3 weeks | Review during next maintenance window |
| **Low** | 1 month | Review with regular version updates |

### Security Advisories

- **GitHub Security Advisories**: [View all advisories](https://github.com/asap-protocol/asap-protocol/security/advisories)
- **Dependabot Alerts**: [View dependency alerts](https://github.com/asap-protocol/asap-protocol/security/dependabot)
- **Dependency Graph**: [View dependency insights](https://github.com/asap-protocol/asap-protocol/network/dependencies)

### Monitoring

We continuously monitor dependencies using:
- **Dependabot**: Automated security updates and alerts
- **pip-audit**: Integrated into CI pipeline for vulnerability scanning (Python)
- **npm audit**: Blocking steps on the `quality (web)` CI job for `apps/web/`
- **GitHub Security Advisories**: Public database of known vulnerabilities

### npm audit policy (`apps/web/`)

CI (`quality-web`) runs both of the following after `npm ci` and treats failures as blocking:

```bash
cd apps/web
npm audit --omit=dev --audit-level=moderate
npm audit --audit-level=high
```

- **Production graph** (`--omit=dev`): moderate and above must be clean.
- **Full graph** (including devDependencies): high and above must be clean.
- Prefer range-compatible updates via `npm install` / `npm audit fix` (never `npm audit fix --force`).
- Do **not** downgrade `next` to satisfy transitive advisories. For Next **16.2.12**, pin fixed transitive deps via npm overrides with an unscoped `next` key — a versioned key like `next@16.2.10` pins nested copies of `next` and breaks `npm ci` (ERESOLVE) on every Next bump:

```json
"overrides": {
  "next": { "postcss": "^8.5.23" },
  "sharp": "^0.35.3",
  "fast-uri": "^3.1.5",
  "nanoid": "^3.3.18",
  "postcss": "^8.5.23",
  "brace-expansion": "^5.0.9",
  "ip-address": "^10.3.1",
  "js-yaml": "^4.3.1",
  "undici": "^7.29.0"
}
```

The `sharp` override forces Next's nested `sharp@0.34.x` up to `>=0.35.0` (GHSA-f88m-g3jw-g9cj / libvips). Keep the direct `sharp` dependency aligned.

If a PostCSS or sharp override breaks `next build`, stop and report — do not silently adopt a preview/canary Next.

**Prettier**: CI also runs `npm run format:check` only on changed TS/TSX under `apps/web/` (`fetch-depth: 0`). On pull requests the base is the PR base SHA; on pushes it is `github.event.before` (the tip before the push), not `merge-base(HEAD, origin/main)` — that merge-base equals `HEAD` on `main` and would skip the check. Full-tree Prettier is intentionally not gated (historical drift). Pass paths locally: `npm run format:check -- <files>`.

CI runs `pip-audit` after a sync that **excludes** the optional extras `crewai` and `llamaindex`, because those graphs currently pull transitive packages (`diskcache`, `nltk`) that OSV still lists with no fixed release on PyPI. To match the security job locally:

`uv sync --frozen --all-extras --dev --no-extra crewai --no-extra llamaindex` then `uv run pip-audit --ignore-vuln CVE-2026-4539 --ignore-vuln CVE-2026-4963 --ignore-vuln CVE-2026-2654 --ignore-vuln PYSEC-2024-271 --ignore-vuln PYSEC-2026-89 --ignore-vuln PYSEC-2025-183` (matches CI).

Security floors live in `tool.uv.override-dependencies` (with per-CVE comments in `pyproject.toml`) so lock refreshes cannot regress them; recent additions: `pymdown-extensions>=11.0.1` (CVE-2026-61632), `pyasn1>=0.6.4` (PYSEC-2026-3455/3456/3457), `aiohttp>=3.14.3` (PYSEC-2026-3545/3546/3547), and `h2>=4.4.1` (PYSEC-2026-3628).

**CVE-2026-4539 (Pygments)**: CI uses `--ignore-vuln CVE-2026-4539` until a patched `pygments` release on PyPI resolves the advisory (`tool.uv.override-dependencies` prefers `pygments>=2.20.0` when resolvable).

**PYSEC-2026-161 (starlette, FastAPI stack)**: Resolved by requiring `fastapi>=0.136.1`, which pulls `starlette>=1.0.1` (Host header path validation). **CVE-2026-54282 / CVE-2026-54283** require `starlette>=1.3.1` via `tool.uv.override-dependencies`. Do not add a `pip-audit` ignore for these advisories.

**CVE-2026-53538–53540 (python-multipart, FastAPI stack)**: Resolved via override (`python-multipart>=0.0.31`).

**GHSA-537c-gmf6-5ccf / PYSEC-2026-3552–3554 (cryptography)**: Resolved by raising the pin to `cryptography>=50.0.0,<51` (direct dependency and override). 49.0.0 covers PYSEC-2026-3553/3554; **PYSEC-2026-3552** (PKCS7 Bleichenbacher) requires 50.0.0. Pair with `pyopenssl>=26.4.0` so the optional `[webauthn]` extra stays importable. Ed25519 usage in `asap.crypto` is unchanged across 48→50.

**CVE-2026-48990 (joserfc)**: Resolved by raising the pin to `joserfc>=1.6.7,<2` (direct dependency and override).

**CVE-2026-48802 / CVE-2026-48809 (python-engineio, locust stack)**: Resolved via override (`python-engineio>=4.13.2`).

**CVE-2026-48804 (python-socketio, locust stack)**: Resolved via override (`python-socketio>=5.16.2`).

**PYSEC-2026-2132 (click)**: Resolved via override (`click>=8.3.3`) — transitive via typer / uvicorn / mkdocs stacks.

**PYSEC-2026-2253–2257 (pillow)**: Resolved via override (`pillow>=12.3.0`; prior floor was `>=12.2.0` for CVE-2026-40192) — transitive via pdf/vision stacks.

**CVE-2026-46678 / PYSEC-2026-3692 / PYSEC-2026-3693 (pydantic-ai, optional `[pydanticai]` extra)**: Resolved via `[pydanticai]` extra floor `pydantic-ai>=1.106.0,<2` (prior floor `>=1.99.0`; 1.102.0 in lock as of 2026-06).

**CVE-2026-4963 / CVE-2026-2654 (smolagents, optional `[smolagents]` extra)**: OSV reports these against current PyPI releases with **no `fix_versions`/`fixed` range** yet. CI ignores them until Hugging Face publishes patched `smolagents` wheels; remove the flags when `pip-audit` is clean without them. The reference package does not import smolagents unless that extra is installed.

**CVE-2026-45409 (idna)**: Resolved via `tool.uv.override-dependencies` (`idna>=3.15`) — DoS in `idna.encode()` on oversized inputs.

**CVE-2026-46338 (pymdown-extensions)**: Resolved via override (`pymdown-extensions>=10.21.3`) and `[docs]` extra floor — snippets `restrict_base_path` prefix bypass in mkdocs stack.

**PYSEC-2026-89 (markdown, mkdocs stack)**: CI uses `--ignore-vuln PYSEC-2026-89` while OSV still lists **3.10.2** (latest on PyPI as of 2026-05) with no fixed release; override pins `markdown>=3.10.2`. Remove the flag when `pip-audit` passes without it.

**PYSEC-2025-183 (pyjwt, transitive via `[mcp]`)**: CI uses `--ignore-vuln PYSEC-2025-183` — advisory is **disputed by the supplier** (minimum key length is application-defined); override already pins `pyjwt>=2.12.0,<3` for CVE-2026-32597.

**CVE-2026-52869 / CVE-2026-52870 / CVE-2026-59950 (mcp, optional `[mcp]` extra)**: Resolved by raising the `[mcp]` floor to `mcp>=1.28.1` (lock 1.28.1 as of 2026-07-18).

**PYSEC-2024-271 (flask-cors, transitive via `locust` in dev/benchmarks)**: CI uses `--ignore-vuln PYSEC-2024-271` — log-injection when debug logging is enabled; **6.0.2 is latest on PyPI** with no fixed release listed. Not on the runtime agent-server path.

**pip**: `tool.uv.override-dependencies` requires `pip>=26.2` so **PYSEC-2026-3721** (and earlier **CVE-2026-3219** / **PYSEC-2026-196**) no longer require a `pip-audit` ignore.

**PYSEC-2026-3447 (setuptools)**: Resolved via override (`setuptools>=83.0.0`) — transitive build/tooling path; `82.0.0` is the last vulnerable release listed by OSV.

If you install `[crewai]` or `[llamaindex]`, run `pip-audit` separately on that environment and expect possible advisories until upstream publishes patched releases. Integration tests for those extras still run in CI with the full dependency tree.

**Optional `[telemetry]` extra**: Weekly PyPI stats use `pypistats`, declared under `[telemetry]` in `pyproject.toml` so default installs avoid that dependency graph. The composite `.github/actions/setup-python` step runs `uv sync --frozen --all-extras --dev`, which installs `[telemetry]` for workflows that use it (including the weekly telemetry job). When auditing **without** `--all-extras`, refresh an environment that includes telemetry before judging related CVEs:

`uv sync --frozen --extra telemetry --dev` then `uv run pip-audit` (reuse the same `--ignore-vuln` flags as in the Monitoring section above when you need parity with CI).

### Version Update Schedule

- **Security Updates**: Automatic and immediate (no schedule)
- **Version Updates**: Monthly checks for non-security updates. This project relies on [Dependabot](.github/dependabot.yml) to monitor and bump dependency versions.

For more information on reviewing Dependabot PRs, see [CONTRIBUTING.md](../CONTRIBUTING.md#reviewing-dependabot-prs).

## Dependency policy

We pin **upper bounds** on the security- and protocol-sensitive libraries listed below to prevent silent major-version bumps through `pip install -U`. Major releases of these packages change cryptographic primitives, JWT validation semantics, or WebAuthn verification contracts — we want an explicit review before absorbing that change.

| Package | Pin | Why |
|---------|-----|-----|
| `cryptography` | `>=50.0.0,<51` | PYSEC-2026-3552 baseline (PKCS7); v47+ serialization API (Rust backend migration) |
| `authlib` | `>=1.6.11,<2` | GHSA-jj8c-mmj3-mmgv baseline; v2 reworks JWS header policy |
| `joserfc` | `>=1.6.7,<2` | CVE-2026-48990 baseline; JWT / JWS / JWE primitives powering Host JWT verification |
| `pyjwt` (override) | `>=2.12.0,<3` | CVE-2026-32597 baseline; v3 changes default `options` behavior for token introspection |
| `webauthn` (optional extra) | `>=2.6,<3` | Assertion/attestation verification for SELF-002 |
| `pydantic` | `>=2.12.5,<3` | Model validation contract across all payloads; v3 is a breaking rewrite |

**Bumping a pinned upper bound**:
1. Read the upstream release notes and diff the deprecations / security-impacting changes
2. Run the full `pytest` + `mypy` + Compliance Harness v2 against the new major
3. Update both `pyproject.toml` and the table above in the same PR
4. Add a CHANGELOG entry under `### Changed` describing the audit

Other dependencies (fastapi, httpx, uvicorn, structlog, opentelemetry-*) are intentionally **unpinned at the top** to keep resolution flexible for downstream consumers. Dependabot bumps are reviewed per the response-time table above, but they do not carry the same cryptographic-primitive risk.
