# Telemetry runbook (weekly adoption snapshot)

Maintainers use `scripts/telemetry/` to build an **aggregate JSON snapshot** plus a **markdown dashboard** for adoption signals (npm/PyPI/GitHub/registry, optional site CTR, and **adapter-request** issue counts). Outputs default to **`private/telemetry/`** (gitignored — see repo `.gitignore`).

There is **no public live metrics dashboard** and **no** public `/metrics` route. The only web surface is bearer-protected ingestion/read at `/api/telemetry` (operator use).

## Prerequisites

- **[uv](https://docs.astral.sh/uv/)** per `pyproject.toml`
- **Network** access to npm, PyPI Stats (`pypistats` extra), GitHub API, and the registry mirror URL

Sync dependencies (includes the `telemetry` extra):

```bash
uv sync --frozen --all-extras --dev
```

The above command installs Python deps from the lockfile and enables the PyPI collector (`pypistats`).

## Tokens and secrets

| Variable | Required | Purpose |
|----------|----------|---------|
| `TELEMETRY_GITHUB_TOKEN` | **Yes** for full aggregate / CI | Maintainer PAT used by `aggregate.py` (preferred over `GITHUB_TOKEN`). Needs access to repo **traffic** (clones/referrers), **issues** (labeled `adapter-request`), and summary endpoints. Fine-grained: resource owner **`asap-protocol`**, repository **`asap-protocol/asap-protocol`**, **Administration: Read** (traffic endpoints), **Issues: Read**, **Metadata: Read**. Classic PAT: **`repo`** (traffic needs push-equivalent access). Store as a **repository Actions secret** named `TELEMETRY_GITHUB_TOKEN`. Without it the aggregate exits 1 before any collector runs. **Never commit this token.** |
| `GITHUB_TOKEN` | Fallback only | Accepted by `collect_github.py` and by `aggregate.py --allow-github-skip` when `TELEMETRY_GITHUB_TOKEN` is unset. The default Actions `GITHUB_TOKEN` **cannot** read traffic; do not rely on it for promotion gates. |
| `TELEMETRY_TOKEN` | For `/api/telemetry` only | Shared bearer for the Next.js route `apps/web/src/app/api/telemetry/route.ts`. Set in Vercel/hosting env; use the same value when calling the route locally or from `aggregate.py`. |
| `TELEMETRY_SITE_ENDPOINT` | Optional | Full HTTPS URL to the deployed telemetry route, e.g. `https://example.com/api/telemetry`. Passed to `aggregate.py` or set as a CI **repository variable**. |
| `TELEMETRY_SITE_METRICS_JSON` | Optional (web app) | Non-secret JSON on the **Next.js server** merged into `ctr_per_cta` (dashboard export or drain). Empty/`{}` is valid when no site metrics are available. |

### Site metrics without a public analytics API

Vercel Web Analytics **does not** expose aggregate event/CTR APIs for arbitrary server-side pulls (see Vercel docs and community threads). The web route therefore supports:

- Optional env **`TELEMETRY_SITE_METRICS_JSON`** on the **Next.js server** — non-secret JSON merged into `ctr_per_cta` (for example values pasted from a dashboard export or a drain pipeline).

CI can omit `TELEMETRY_SITE_ENDPOINT`; the snapshot will still include an empty `site.ctr_per_cta` with a note.

## Guide-view proxies (not MkDocs analytics)

**Guide views** are estimated with existing collectors — **not** a MkDocs analytics plugin:

1. **GitHub traffic / referrers** — `scripts/telemetry/collect_github.py` (repo views and referrer paths; useful when docs traffic lands via GitHub).
2. **Site → docs CTR proxies** — homepage/`data-cta` click and view counts via `/api/telemetry` (Bearer `TELEMETRY_TOKEN`), optionally seeded by `TELEMETRY_SITE_METRICS_JSON`.

**Out of scope:** installing or configuring a MkDocs analytics plugin, public dashboards, or new `/metrics` UI under `apps/web`.

## Default package coverage (DIST-004)

When package flags are omitted, collectors/aggregate use:

| Source | Defaults |
|--------|----------|
| npm | `@asap-protocol/client`, `@asap-protocol/mastra`, `@asap-protocol/mcp-auth`, `@asap-protocol/openai-agents` |
| PyPI | `asap-protocol`, `asap-compliance` |

The npm API answers HTTP 404 for a package that is not on npm, and the npm collector then fails the whole run. So a new package must be on npm before its default reaches the weekly job on `main`.

## Running collectors individually

**npm** (public API; four scoped packages by default):

```bash
uv run python scripts/telemetry/collect_npm.py --period last-week -o /tmp/npm.json
```

**PyPI** (`pypistats`; `asap-protocol` + `asap-compliance` by default):

```bash
uv run python scripts/telemetry/collect_pypi.py -o /tmp/pypi.json
```

**GitHub** (summary, traffic, referrers, **open `adapter-request` issues**):

```bash
export TELEMETRY_GITHUB_TOKEN=…   # preferred; GITHUB_TOKEN also accepted by this script
uv run python scripts/telemetry/collect_github.py -o /tmp/github.json
```

**Registry** (agent count + optional `--previous`):

```bash
uv run python scripts/telemetry/collect_registry.py -o /tmp/registry.json
```

## Weekly aggregate (snapshot + dashboard)

```bash
export TELEMETRY_GITHUB_TOKEN=…
export TELEMETRY_TOKEN=…   # optional if fetching site
export TELEMETRY_SITE_ENDPOINT=https://YOUR_HOST/api/telemetry   # optional
uv run python scripts/telemetry/aggregate.py --output-dir private/telemetry
```

Local dry-run **without** GitHub traffic (placeholders only — not for promotion gates):

```bash
uv run python scripts/telemetry/aggregate.py --output-dir private/telemetry --allow-github-skip
```

The script:

- Collects the default npm (≥3) and PyPI (≥2) package sets.
- Retries PyPI Stats HTTP 429, 5xx, and connection errors per package (4 attempts, waits of 10 s, 30 s, 90 s). If PyPI still fails, the snapshot keeps `pypi.packages` empty, sets `pypi.fetch_error: true` with a generic `pypi.reason`, and `dashboard.md` lists it under **Data quality warnings**. The run still exits 0.
- Fails closed (exit 1, no snapshot) when `TELEMETRY_GITHUB_TOKEN` is missing or a GitHub call fails, unless `--allow-github-skip` is passed. Adapter-request counts feed promotion decisions, so CI never writes placeholder zeros.
- Validates the snapshot against an embedded **JSON Schema** (`jsonschema`).
- Writes `snapshot-YYYY-MM-DD.json`, `dashboard.md`, and attempts `snapshot-latest.json` → symlink (may fail on Windows without symlink rights — optional).

## Adapter-request issues

Ensure the GitHub label **`adapter-request`** exists on the repository (Settings → Labels) so the issue template and automation apply it correctly.

Issues labeled **`adapter-request`** are grouped by **framework name** parsed from the issue body:

1. GitHub Issue Form heading: `### Framework name` (or `### Framework`) followed by the value on the next line.
2. Free-form line: `Framework: My Framework Name` (case-insensitive).

The aggregator stores **slugified** keys (e.g. `OpenAI Agents SDK` → `openai-agents-sdk`). Issues that cannot be parsed increment **`_unparsed`** in the snapshot and dashboard.

## CI workflow (weekly schedule + dispatch)

Workflow **`.github/workflows/telemetry-weekly.yml`**:

- **Triggers:** `schedule` (`0 9 * * 1`, Mondays 09:00 UTC) and `workflow_dispatch` (Actions → Telemetry (weekly) → Run workflow).
- **Requires** the repository secret `TELEMETRY_GITHUB_TOKEN`. Optional: variable `TELEMETRY_SITE_ENDPOINT` and secret `TELEMETRY_TOKEN`.
- The workflow **does not push to `main`**; it uploads **`snapshot-*.json`** and **`dashboard.md`** as a **workflow artifact** (`telemetry-<run_id>`).

The schedule failed every Monday from 2026-05-18 to 2026-06-08: the secret was missing, and one run stopped on PyPI Stats HTTP 429. The workflow was then disabled in GitHub. To turn it back on:

1. Add the `TELEMETRY_GITHUB_TOKEN` secret (permissions in the table above).
2. Enable the workflow: `gh workflow enable telemetry-weekly.yml --repo asap-protocol/asap-protocol`.
3. Run it once: `gh workflow run telemetry-weekly.yml --repo asap-protocol/asap-protocol`. Check that the artifact has real GitHub data and no **Data quality warnings** you did not expect.

While the workflow is disabled in GitHub, neither the schedule nor dispatch runs.

Optional: download the artifact and copy files to a **private branch** if you want historical copies in git (not required for promotion-gate review). Do not invent dashboard numbers.

## Revoking access

- **Rotate `TELEMETRY_GITHUB_TOKEN`** (and any fallback `GITHUB_TOKEN` PAT) at the source (GitHub developer settings) and update Secrets/ENV wherever it was stored.
- **Rotate `TELEMETRY_TOKEN`** in the hosting provider, then update any automation (`TELEMETRY_TOKEN` / `TELEMETRY_SITE_ENDPOINT`) that calls `/api/telemetry`.

## Relevant code

- `scripts/telemetry/aggregate.py` — join + validate + `dashboard.md`
- `scripts/telemetry/collect_npm.py` / `collect_pypi.py` — package download defaults
- `scripts/telemetry/collect_github.py` — traffic, referrers, `adapter_requests`
- `apps/web/src/app/api/telemetry/route.ts` — bearer-protected site metrics hook (ingestion/read only)
