# Hotfix runbook

Maintainer guide for shipping an urgent fix outside the planned releases. Since v2.6.0 each npm package publishes from its own tag, so a hotfix moves only the package that needs it; the other packages and the Python version stay where they are. Read [npm-publishing.md](./npm-publishing.md) for OIDC, Trusted Publishing, and `NPM_TOKEN_EMERGENCY` before executing a hotfix.

## Maintainer cross-links

- **When to release at all** (minor vs maintenance patch vs hotfix): [release-policy.md](./release-policy.md)
- **npm publish (normal + emergency)**: [npm-publishing.md](./npm-publishing.md)
- **Release checklist (v2.3.1)**: [engineering/tasks/private/v2.3.1/release-checklist.md](../../engineering/tasks/private/v2.3.1/release-checklist.md) — §4.0 rollback points here
- **Publish CI**: [.github/workflows/publish-typescript.yml](../../.github/workflows/publish-typescript.yml) — tags `client-v*` / `mcp-auth-v*` (npm); [.github/workflows/release.yml](../../.github/workflows/release.yml) — tags `v*` (PyPI and Docker)

## § Decision: hotfix now vs wait for next minor

The [release policy](./release-policy.md#when-to-hotfix) sets when a hotfix is allowed (an exploitable advisory or a broken Tier 1 package). If this table and the policy disagree, the policy wins.

| Signal | Prefer **hotfix** | Prefer **wait** (next minor or monthly patch) |
|--------|-------------------|-----------------------------------------------------|
| Severity | Published adapter broken for all consumers (install fails, runtime crash on documented path, security advisory) | Cosmetic docs, non-blocking DX, internal examples only |
| Blast radius | `@asap-protocol/client`, `mcp-auth`, `mastra`, or `openai-agents` on npm | Unpublished code, private examples, maintainer-only scripts |
| Protocol contract | Envelope / JSON-RPC / shared types wrong on wire | Adapter-only bug with unchanged protocol surface |
| Workaround | None or unsafe (data loss, auth bypass) | Pin older version, document workaround, feature flag |
| Timeline | Fix needed within days | Can ride the next planned minor with full test matrix |

**Default**: if the issue does not affect a **published** npm package on a **supported** code path, schedule the fix in the next minor rather than opening a hotfix branch.

## § Version scheme

Each package gets the next patch of its own version. Bump only the packages that change.

| What ships | Version and tag | Workflow |
|------------|-----------------|----------|
| An npm package | Next patch, for example `@asap-protocol/client` `2.5.0` → `2.5.1`, tag `client-v2.5.1` | `publish-typescript.yml`, dist-tag `latest` |
| An npm package, as a candidate to try first | A prerelease, for example tag `mcp-auth-v0.1.1-rc.1` | `publish-typescript.yml`, dist-tag `rc` |
| The Python package `asap-protocol` | Next patch, for example `2.5.11` → `2.5.12`, tag `v2.5.12` | `release.yml` (PyPI and Docker) |

A package with no tag prefix yet (`mastra`, `openai-agents`) needs one before it can ship; see [npm-publishing.md](./npm-publishing.md).

**Retired track.** Until v2.4.x one tag set the version of every npm package, so an adapter-only fix shipped as a prerelease such as `2.3.1-hotfix.1` under the `hotfix` dist-tag, through `hotfix-release.yml`. Per-package tags make that track unnecessary, and the workflow was removed in v2.6.0. Versions already on npm under `hotfix` stay there.

**Do not** republish the same version to npm. If a hotfix publish fails mid-flight, fix forward with the next version (for example `2.5.2`).

## § Branching strategy

1. Identify the last good release tag of the package (for example `client-v2.5.0`; `v*` for Python):

   ```bash
   git fetch --tags
   git tag -l 'client-v*'
   ```

2. Create a hotfix branch **from that tag** (the name carries the package and the target version):

   ```bash
   git checkout -b hotfix/client-2.5.1 client-v2.5.0
   ```

3. Implement the minimal fix; run the same gates as release (see [release-checklist.md](../../engineering/tasks/private/v2.3.1/release-checklist.md) §1): `pnpm test`, `pnpm typecheck`, `pnpm lint`, and adapter compliance if touched.

4. Bump the version per § Version scheme, in the affected package only. Check the tag before you push it: `python3 scripts/npm_publish_target.py client-v2.5.1` must print the package, or it names the mismatch.

5. Add a **public** CHANGELOG subsection (factual, no private PRD context).

## § Cherry-pick workflow

After the hotfix tag is published and verified:

1. Merge or cherry-pick the hotfix commit(s) onto `main` so the fix is not lost on the next minor:

   ```bash
   git checkout main
   git pull origin main
   git cherry-pick <commit-sha>   # repeat per commit, or merge hotfix/client-2.5.1
   ```

2. On `main`, keep the package version at or above the hotfix version, so the next release of that package does not reuse it.

3. Resolve conflicts in `pnpm-lock.yaml` and integration docs; re-run CI.

4. If the fix shipped only as an `rc` prerelease, document in CHANGELOG that consumers on `latest` get it with the next stable version.

## § Publish (automated)

### Trusted Publishing

A hotfix uses the same workflow and the same npm Trusted Publisher as a normal release: `.github/workflows/publish-typescript.yml`, registered for each published package (see [npm-publishing.md](./npm-publishing.md)).

### Tag and push

```bash
git tag -a client-v2.5.1 -m "Hotfix: <one-line summary>"
git push origin hotfix/client-2.5.1
git push origin client-v2.5.1
```

The workflow publishes only the package that the tag names, and fails if the tag differs from its `package.json` version. A Python patch is tagged `v2.5.12` and goes through `release.yml`.

### Verify

```bash
npm view @asap-protocol/client dist-tags
# expect latest == 2.5.1 (or rc == the version, for a prerelease)
```

## § Emergency manual publish (OIDC broken)

If Trusted Publishing fails during a hotfix window:

1. Follow [npm-publishing.md § Emergency manual publish](./npm-publishing.md#-emergency-manual-publish-oidc-broken) using **`NPM_TOKEN_EMERGENCY`** (never commit the token).
2. Use the dist-tag the workflow would choose: `--tag rc` for a prerelease, otherwise the default `latest`:

   ```bash
   npm publish --access public --provenance=false --tag rc   # prerelease only
   ```

3. Restore OIDC before the next tag; confirm `publish-typescript.yml` is registered on npm for all affected packages.

## § CI: dry-run vs tag-only publish

| Event | Workflow behavior |
|-------|-------------------|
| **Pull request** to `main` (paths: TS packages, the tag script, the publish workflow) | `npm publish --dry-run` for all four packages, and the tag-rule check — validates pack contents without registry write or `id-token` |
| **Push** tag `client-v*` or `mcp-auth-v*` | Real publish of that one package with OIDC + provenance |

Real publishes are **tag-only** because npm Trusted Publishing and provenance are bound to release tags, not branch heads. Dry-run on PR is the pre-flight gate; do not rely on branch pushes to publish. A pull request into a `hotfix/*` branch gets no dry run, so run `npm publish --dry-run` and the tag check from step 4 of § Branching strategy locally before you push the tag.

## § Post-mortem template

Copy into a private maintainer doc or internal issue after any production-impacting hotfix:

```markdown
## Hotfix post-mortem — <package> <version>

**Date**:
**Incident owner**:
**Versions shipped** (npm tags / dist-tags):
**Tags** (for example `client-v2.5.1`, `v2.5.12`):

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

- [npm-publishing.md](./npm-publishing.md) — OIDC, `NPM_TOKEN_EMERGENCY`, per-package tags
- [npm Trusted Publishing](https://docs.npmjs.com/trusted-publishers)
- [Semantic Versioning — prerelease](https://semver.org/#spec-item-9)
