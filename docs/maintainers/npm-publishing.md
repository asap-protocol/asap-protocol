# npm publishing runbook (`@asap-protocol/client`)

Maintainer guide for scoped package bootstrap, Trusted Publishing (OIDC), and recovery when the registry or OIDC path fails. Read this before rotating secrets or doing a break-glass publish; no prior npm-org context is assumed beyond repo membership.

## Maintainer cross-links

- **GitHub org cutover**: [github-organization.md](./github-organization.md) — Trusted Publisher owner/repo, secrets, Pages, GHCR after transfer to `asap-protocol`.
- **Release checklist (npm gate)**: [engineering/tasks/v2.3.0/release-checklist.md](../../engineering/tasks/v2.3.0/release-checklist.md) — §**4.3** (verify install after publish).
- **Hotfix runbook**: [hotfix.md](./hotfix.md) — a patch of one package from its own tag, branching, cherry-pick, emergency publish.
- **Sprint notes (historical)**: [engineering/tasks/private/v2.3.1/sprint-S0-unblock-npm.md](../../engineering/tasks/private/v2.3.1/sprint-S0-unblock-npm.md) — first-publish / OIDC troubleshooting narrative.

## Normal path (after bootstrap)

- **Tags (one per package)**: since v2.6.0, each package publishes from its own tag. [.github/workflows/publish-typescript.yml](../../.github/workflows/publish-typescript.yml) runs `scripts/npm_publish_target.py`, which maps the tag to its package, and publishes only that package. The version comes from the package's `package.json`; the workflow never rewrites it. If the tag version differs from `package.json`, the job fails before it builds.

  | Tag | Publishes |
  |-----|-----------|
  | `client-v<version>`, for example `client-v2.5.0` | `@asap-protocol/client` |
  | `mcp-auth-v<version>`, for example `mcp-auth-v0.1.0` | `@asap-protocol/mcp-auth` |

  The Python tag `v<version>` runs `release.yml` (PyPI and Docker) and publishes no npm package; its `v*` pattern does not match the npm tags. The `v2.3.*` and `v2.4.*` triggers are retired: they set one version on every package. `mastra` and `openai-agents` have no tag in v2.6.0 because they are not republished. To add a package, add an entry to `PACKAGES_BY_TAG_PREFIX` in the script, add its tag pattern to the workflow, and register the Trusted Publisher for it.
- **Order**: bump `package.json` in a commit, merge it, then tag that commit and push the tag. Publish `client-v2.5.0` before `mcp-auth-v0.1.0`, because `mcp-auth` has the peer dependency `@asap-protocol/client` `^2.5.0`.
- **Published dependency ranges**: `dependencies`, `peerDependencies`, and `optionalDependencies` must not hold a `workspace:` range, because `npm publish` does not rewrite it. The script refuses such a tag.
- **Toolchain (GitHub-hosted)**: Trusted Publishing needs **npm CLI ≥ 11.5.1** and **Node.js ≥ 22.14** (see [npm: Trusted publishers](https://docs.npmjs.com/trusted-publishers/)). The workflow pins Node **22** and runs `npm install -g npm@11.5.1` before publish.
- **Auth**: The `publish` job uses `permissions: id-token: write` and `npm publish --provenance`. **Do not** set `actions/setup-node` `registry-url` for the publish job unless you also supply a valid npm token only where needed — the default `NODE_AUTH_TOKEN` is `GITHUB_TOKEN`, which is **not** a valid npm credential and causes misleading **404** errors during publish.
- **PRs**: Same workflow runs `npm publish --dry-run` only, for all four packages, and checks `client` and `mcp-auth` against their tag rule (no `id-token` required for that job path).
- **Prereleases**: A version with a semver prerelease segment (for example `mcp-auth-v0.1.0-rc.1`) publishes under the **`rc`** dist-tag, so `latest` never moves to a prerelease. A stable version publishes under `latest`.

## § Bootstrap (one-time)

### 1. Org scope

- Ensure the npm org exists and maintainers are **owners** or members with publish rights.
- Check: `npm login` then `npm org ls @asap-protocol` (expect your user as `owner` or appropriate role).

### 2. First publish (package must exist before Trusted Publishing)

npm may return **404** on first `PUT` to `@asap-protocol/client` until the package exists. Publish once from a maintainer machine:

```bash
cd packages/typescript/client
pnpm install --frozen-lockfile
pnpm run build
npm publish --access public --provenance=false
```

Use a **short-lived automation token** with publish scope; **revoke it** after success.

Verify:

```bash
npm view @asap-protocol/client version
```

### 3. Trusted Publishing (GitHub Actions)

1. Open [Package access — @asap-protocol/client](https://www.npmjs.com/package/@asap-protocol/client/access). Repeat these steps for every package that publishes from a tag, such as `@asap-protocol/mcp-auth`.
2. Under **Trusted Publishers** → **GitHub Actions**, register:
   - **Repository**: `asap-protocol/asap-protocol`
   - **Workflow file**: `.github/workflows/publish-typescript.yml`
   - **Environment** (if used on GitHub): match the workflow (often none; if you add a `release` environment later, it must match here).

Allow several minutes for registry propagation after saving.

## § Secrets and rotation — `NPM_TOKEN_EMERGENCY`

**Convention**

- **Do not** store a routine publish token as `NPM_TOKEN` in GitHub. Normal releases use OIDC only.
- **Do** keep one automation token as **`NPM_TOKEN_EMERGENCY`** for outages (npm OIDC incidents, misconfiguration recovery). Treat it as **break-glass**: 90-day (or shorter) expiry, minimal scope, rotated on schedule.

**Rotation (quarterly or after any suspected leak)**

1. In npmjs.com, create a new **automation** token with publish access to `@asap-protocol/client`.
2. In the GitHub repo **Settings → Secrets and variables → Actions**, update **`NPM_TOKEN_EMERGENCY`** (or create it if missing).
3. Remove or disable the previous token in npm.
4. Add a calendar reminder for the next rotation date.

**Verify**

- No secret named `NPM_TOKEN` is required for the current workflow (it does not reference a token).
- If a legacy `NPM_TOKEN` secret still exists from earlier experiments, delete or rename it so **`NPM_TOKEN_EMERGENCY`** is the only stored npm classic credential.

## § Emergency manual publish (OIDC broken)

When Trusted Publishing or GitHub OIDC fails but you must ship a fix:

1. Use `NPM_TOKEN_EMERGENCY` locally (or a fresh short-lived token); never commit it.
2. From `packages/typescript/client` after a clean build:

   ```bash
   npm config set //registry.npmjs.org/:_authToken "$NPM_TOKEN_EMERGENCY"
   npm publish --access public --provenance=false
   ```

3. Revoke any one-off token if you minted a new token instead of the emergency secret.
4. Restore OIDC path: fix workflow or npm Trusted Publisher settings before the next tag.

## References

- [hotfix.md](./hotfix.md) — post-release hotfix decision tree and tags
- [npm Trusted Publishing](https://docs.npmjs.com/trusted-publishers)
- [GitHub: npm provenance](https://docs.github.com/en/actions/publishing-packages/publishing-nodejs-packages#publishing-packages-to-the-npmjs-registry)
