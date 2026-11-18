# Hotfix runbook (v2.3.x line)

Maintainer guide for shipping urgent fixes after a **v2.3.x** TypeScript release without disturbing `latest` on npm or the Python protocol version when only adapters need a patch. Read [npm-publishing.md](./npm-publishing.md) for OIDC, Trusted Publishing, and `NPM_TOKEN_EMERGENCY` before executing a hotfix.

## Maintainer cross-links

- **When to release at all** (minor vs maintenance patch vs hotfix): [release-policy.md](./release-policy.md)
- **npm publish (normal + emergency)**: [npm-publishing.md](./npm-publishing.md)
- **Release checklist (v2.3.1)**: [engineering/tasks/private/v2.3.1/release-checklist.md](../../engineering/tasks/private/v2.3.1/release-checklist.md) — §4.0 rollback points here
- **Hotfix CI**: [.github/workflows/hotfix-release.yml](../../.github/workflows/hotfix-release.yml) — tags `v2.3.*-hotfix.*` / `v2.3.*-rc.*`

## § Decision: hotfix now vs wait for next minor

The [release policy](./release-policy.md#when-to-hotfix) sets when a hotfix is allowed (an exploitable advisory or a broken Tier 1 package). If this table and the policy disagree, the policy wins.

| Signal | Prefer **hotfix** | Prefer **wait** (next minor, e.g. v2.3.2 / v2.4.0) |
|--------|-------------------|-----------------------------------------------------|
| Severity | Published adapter broken for all consumers (install fails, runtime crash on documented path, security advisory) | Cosmetic docs, non-blocking DX, internal examples only |
| Blast radius | `@asap-protocol/client`, `mastra`, or `openai-agents` on npm | Unpublished code, private examples, maintainer-only scripts |
| Protocol contract | Envelope / JSON-RPC / shared types wrong on wire | Adapter-only bug with unchanged protocol surface |
| Workaround | None or unsafe (data loss, auth bypass) | Pin older version, document workaround, feature flag |
| Timeline | Fix needed within days | Can ride the next planned minor with full test matrix |

**Default**: if the issue does not affect a **published** npm package on a **supported** code path, schedule the fix in the next minor rather than opening a hotfix branch.

## § Version scheme

Two tracks — pick one before branching:

### A. Protocol / shared-client fix (bumps patch on the release line)

- Example: `2.3.1` → **`2.3.2`**
- Applies when `@asap-protocol/client` types or behavior change in a way consumers must pick up, or multiple packages must move together.
- **Tag**: `v2.3.2` on `main` (after merge).
- **Publish**: [.github/workflows/publish-typescript.yml](../../.github/workflows/publish-typescript.yml) (stable tag → `latest` on npm).
- **Python**: only bump PyPI `asap-protocol` if the Python core changed; v2.3.x adapter-only releases typically leave PyPI at **2.3.0**.

### B. Adapter-only fix (no protocol version bump)

- Example: `@asap-protocol/mastra@`**`2.3.1-hotfix.1`** (semver prerelease on the **2.3.1** line).
- Same pattern for `@asap-protocol/openai-agents` or, rarely, client-only adapter surface without protocol bump.
- **Tag**: `v2.3.1-hotfix.1` (increment `.N` for subsequent adapter hotfixes: `hotfix.2`, …).
- **Publish**: [.github/workflows/hotfix-release.yml](../../.github/workflows/hotfix-release.yml) → npm dist-tag **`hotfix`** (not `latest`).
- **Install**: `npm install @asap-protocol/mastra@hotfix` or exact version `2.3.1-hotfix.1`.

**Do not** republish the same semver to npm. If a hotfix publish fails mid-flight, fix forward with the next version (e.g. `2.3.1-hotfix.2` or `2.3.2`).

## § Branching strategy

1. Identify the last good release tag (e.g. `v2.3.1`):

   ```bash
   git fetch --tags
   git tag -l 'v2.3.*'
   ```

2. Create a hotfix branch **from that tag** (name encodes the fix generation):

   ```bash
   git checkout -b hotfix/2.3.1.1 v2.3.1
   ```

   Use `hotfix/2.3.1.2` for a second hotfix wave on the same base if the first branch was already merged and tagged.

3. Implement the minimal fix; run the same gates as release (see [release-checklist.md](../../engineering/tasks/private/v2.3.1/release-checklist.md) §1): `pnpm test`, `pnpm typecheck`, `pnpm lint`, and adapter compliance if touched.

4. Bump versions per § Version scheme (adapter-only: set `package.json` to `2.3.1-hotfix.1` in affected packages only, or protocol path: `2.3.2` everywhere that ships).

5. Add a **public** CHANGELOG subsection (factual, no private PRD context).

## § Cherry-pick workflow

After the hotfix tag is published and verified:

1. Merge or cherry-pick the hotfix commit(s) onto `main` so the fix is not lost on the next minor:

   ```bash
   git checkout main
   git pull origin main
   git cherry-pick <commit-sha>   # repeat per commit, or merge hotfix/2.3.1.1
   ```

2. On `main`, set package versions to the **next development** target (e.g. `2.3.2` or `2.3.2-dev.0` per team convention — do not leave `2.3.1-hotfix.1` on `main` unless intentionally staging another hotfix).

3. Resolve conflicts in `pnpm-lock.yaml` and integration docs; re-run CI.

4. If the hotfix used dist-tag `hotfix`, document in CHANGELOG that consumers on `latest` should upgrade to the next stable patch when it ships.

## § Publish (automated)

### Trusted Publishing

Register **each** published package on npm → **Trusted Publishers** → **GitHub Actions**:

| Field | Value |
|-------|--------|
| Repository | `asap-protocol/asap-protocol` |
| Workflow file | `.github/workflows/hotfix-release.yml` |
| Environment | (none, unless you add a `release` environment later) |

Allow several minutes after saving. Toolchain matches [npm-publishing.md](./npm-publishing.md): Node **22**, `npm install -g npm@11.5.1`, **no** `registry-url` on `actions/setup-node` for the publish job.

### Tag and push

**Adapter-only (track B):**

```bash
git tag -a v2.3.1-hotfix.1 -m "Hotfix: <one-line summary>"
git push origin hotfix/2.3.1.1
git push origin v2.3.1-hotfix.1
```

**Protocol patch (track A):** merge to `main`, tag `v2.3.2`, push — uses `publish-typescript.yml`, not the hotfix workflow.

### Verify

```bash
npm view @asap-protocol/mastra version
npm view @asap-protocol/mastra dist-tags
# hotfix track: expect dist-tags.hotfix == 2.3.1-hotfix.1
```

## § Emergency manual publish (OIDC broken)

If Trusted Publishing fails during a hotfix window:

1. Follow [npm-publishing.md § Emergency manual publish](./npm-publishing.md#-emergency-manual-publish-oidc-broken) using **`NPM_TOKEN_EMERGENCY`** (never commit the token).
2. For adapter-only versions, publish with the **`hotfix`** dist-tag explicitly:

   ```bash
   npm publish --access public --provenance=false --tag hotfix
   ```

3. Restore OIDC before the next tag; confirm `hotfix-release.yml` is registered on npm for all affected packages.

## § CI: dry-run vs tag-only publish

| Event | Workflow behavior |
|-------|-------------------|
| **Pull request** to `main` (paths: TS packages + hotfix workflow) | `npm publish --dry-run` only — validates pack contents without registry write or `id-token` |
| **Pull request** to `hotfix/*` | Same dry-run job (validates the branch before tag) |
| **Push** tag `v2.3.*-hotfix.*` or `v2.3.*-rc.*` | Real publish with OIDC + provenance |

Real publishes are **tag-only** because npm Trusted Publishing and provenance are bound to release tags, not branch heads. Dry-run on PR is the pre-flight gate; do not rely on branch pushes to publish.

## § Post-mortem template

Copy into a private maintainer doc or internal issue after any production-impacting hotfix:

```markdown
## Hotfix post-mortem — v2.3.x

**Date**:
**Incident owner**:
**Versions shipped** (npm tags / dist-tags):
**Track** (A protocol patch / B adapter-only):

### Summary
(2–3 sentences: what broke, who was affected.)

### Timeline
| Time (UTC) | Event |
|------------|--------|
| | Detection |
| | Branch opened |
| | Fix merged / tagged |
| | npm verified |

### Root cause
(Technical cause; link PR/commit.)

### Why hotfix vs minor
(One paragraph referencing § Decision criteria.)

### What went well / what to improve
-

### Follow-ups
- [ ] Cherry-pick on `main` confirmed
- [ ] CHANGELOG / migration note (if consumer-facing)
- [ ] Trusted Publisher / workflow registry checked
- [ ] Promotion-gate or telemetry review scheduled (if adoption impact)
```

## References

- [npm-publishing.md](./npm-publishing.md) — OIDC, `NPM_TOKEN_EMERGENCY`, normal `v2.3.*` tags
- [npm Trusted Publishing](https://docs.npmjs.com/trusted-publishers)
- [Semantic Versioning — prerelease](https://semver.org/#spec-item-9)
