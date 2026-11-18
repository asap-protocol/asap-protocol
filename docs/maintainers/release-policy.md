# Release and versioning policy

Maintainer policy for when to cut a release and which version number it gets. It applies to the PyPI packages `asap-protocol` and `asap-compliance` and to the npm packages under `@asap-protocol/*`. Each package keeps its own version number. The same rules apply to each.

Adopted **2026-11-18**, when the v2.5.x train closed at **2.5.11**. From that date, 2.5.x gets maintenance patches only, under the rules below.

## Maintainer cross-links

- **Hotfix mechanics** (branching, tags, npm dist-tags): [hotfix.md](./hotfix.md)
- **Release history**: [CHANGELOG.md](https://github.com/asap-protocol/asap-protocol/blob/main/CHANGELOG.md) and [GitHub Releases](https://github.com/asap-protocol/asap-protocol/releases)
- **Train index** (current version, next trains): [product/prd/prd-roadmap.md](https://github.com/asap-protocol/asap-protocol/blob/main/product/prd/prd-roadmap.md)
- **What each integration promises**: [Integration support tiers](../integrations/support-tiers.md)
- **SemVer and protocol version background**: [ADR-016 Versioning policy](../adr/ADR-016-versioning-policy.md)

## Release types

| Release | Example | Carries | Never carries |
|---------|---------|---------|---------------|
| **Minor** (`x.Y.0`) | `2.6.0` | Planned PRD scope: new endpoints, models, fields, protocol surface, integrations. Deprecations. Removal of a deprecated integration. | Breaking protocol or wire changes |
| **Patch** (`x.y.Z`) | `2.6.1` | Unplanned maintenance: security fixes, dependency floors, correctness bugs | Planned PRD scope, a codename, new endpoints, models, or protocol surface |
| **Major** (`X.0.0`) | `3.0.0` | Breaking protocol or wire changes | Product tracks that do not change the wire |

A **hotfix** is not a separate release type. It is a patch that ships now, outside the monthly batch, because it cannot wait. See [When to hotfix](#when-to-hotfix).

## Rules

### 1. Planned scope ships only in minor releases

PRD items ship in a minor release (`x.Y.0`). A patch (`x.y.Z`) is unplanned maintenance: security fixes, dependency floors, and correctness bugs.

A patch never:

- carries planned PRD scope;
- gets a codename (train names such as "Adapter Lab" or "Distribution Loop" belong to minors);
- adds an endpoint, a model, a field, or other protocol surface.

If a fix cannot ship without new surface, it goes in the next minor.

### 2. Test-only changes do not trigger a release

Regression-fence PRs and other test-only changes merge to `main`. They ship with the next release that needs one.

The same applies to CI workflows and maintainer scripts, because they never reach an installed package. The docs site and `apps/web/` deploy from `main` on their own; they do not need a library release.

### 3. Dependency floors are batched

Raise dependency floors in **at most one maintenance patch per month**. Merge Dependabot and `pip-audit` floor changes to `main` as they arrive, then release them together. Merging and releasing are separate steps: the review targets in [SECURITY.md](https://github.com/asap-protocol/asap-protocol/blob/main/SECURITY.md#response-times-by-severity) apply to merging.

**Exception:** if an advisory is **exploitable through ASAP's shipped code paths**, use the [hotfix path](./hotfix.md) and release now. A shipped code path is code in a published package (`src/asap/`, `asap-compliance`, `packages/typescript/*/src`) that reaches the vulnerable dependency code. An advisory that reaches only dev tooling, tests, benchmarks, or the docs build waits for the monthly batch.

### 4. One source of truth for release history

Release history lives in two places:

- [CHANGELOG.md](https://github.com/asap-protocol/asap-protocol/blob/main/CHANGELOG.md): one `## [x.y.z]` section per version. `release.yml` fails a tag that has no matching section, and builds the GitHub Release notes from it.
- [GitHub Releases](https://github.com/asap-protocol/asap-protocol/releases).

Status docs carry only three things: the **current version**, the **next train**, and a **link to the CHANGELOG**. They do not list one row per patch.

| Status doc | Purpose |
|------------|---------|
| `AGENTS.md` | Agent context map |
| `README.md` | Public landing page |
| `product/README.md` | Product folder index |
| `product/prd/README.md` | PRD folder index |
| `product/checkpoints.md` | Post-release documentation checkpoints |
| `engineering/tasks/README.md` | Sprint folder index |
| `docs/index.md` | Docs site home |

When you cut a release, update the version line in each status doc. Write the history in the CHANGELOG only.

### 5. PRDs are named by track, not by version

New PRDs take a track name, for example `prd-interop.md` or `prd-spec-profile.md`. The target version goes in the PRD header. If the target moves, edit the header and keep the filename. The living train index is [prd-roadmap.md](https://github.com/asap-protocol/asap-protocol/blob/main/product/prd/prd-roadmap.md).

Older PRDs keep their versioned filenames as history. If you rename a PRD, leave a redirect stub at the old path so that historical links keep working (example: `prd-v2.5.5-formal-spec-interop.md` now points to `prd-spec-profile.md`).

### 6. A library major is for breaking wire changes

Library major **v3.0.0** is reserved for breaking protocol or wire changes. Commercial services (economy, billing, hosted) are product tracks. They are decoupled from library semver: they do not change the library version unless they change the library.

## When to hotfix

Use the hotfix path (a patch now, outside the monthly batch) when one of these is true:

- An advisory is exploitable through ASAP's shipped code paths.
- A published Tier 1 package is broken on a documented code path (install fails, crash, wrong wire behavior) and there is no safe workaround.

Everything else waits: planned scope for the next minor, maintenance for the next monthly patch. The [hotfix runbook](./hotfix.md) has the branching, tag, and npm dist-tag steps.

Security fixes by [support tier](../integrations/support-tiers.md):

| Tier | Security fix commitment |
|------|-------------------------|
| **Tier 1 — Core** | Fixed on the latest minor line. Hotfix when exploitable; otherwise the next maintenance patch. |
| **Tier 2 — Maintained** | Best effort, in the next maintenance patch. A maintainer may still take the hotfix path for an exploitable advisory; it is not a commitment. |
| **Tier 3 — Experimental** | No commitment. |

## Release decision checklist

Answer the questions in order. Stop at the first "yes".

| # | Question | If yes |
|---|----------|--------|
| 1 | Does the change touch only tests, CI, maintainer scripts, docs, or `apps/web/`? (It ships no code users run.) | **No release.** Merge to `main`. It ships with the next release that needs one. |
| 2 | Is there an advisory exploitable through ASAP's shipped code paths, or a Tier 1 package broken on a documented path with no safe workaround? | **Hotfix.** Patch now; follow [hotfix.md](./hotfix.md). |
| 3 | Does it break the protocol or the wire? | **Major.** Reserved for v3.0.0; needs a recorded decision first. |
| 4 | Is it planned scope (PRD item, new endpoint, model, field, protocol surface, or integration)? | **Minor.** Ship it with its train (see [prd-roadmap.md](https://github.com/asap-protocol/asap-protocol/blob/main/product/prd/prd-roadmap.md)). |
| 5 | Otherwise (non-exploitable security fix, dependency floor, correctness bug) | **Maintenance patch.** Batch it; at most one per month. |

Before you tag a patch, check the diff once more: no PRD scope, no codename, no new endpoint, model, field, or protocol surface.
