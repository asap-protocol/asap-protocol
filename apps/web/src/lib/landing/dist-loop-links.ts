/**
 * Canonical public URLs for homepage CTAs.
 *
 * `blob/main` / `tree/main` are the stable destinations for links that point
 * at repository files and directories.
 */

/** GitHub `blob/main` base for docs links. */
export const GITHUB_BLOB_MAIN = 'https://github.com/asap-protocol/asap-protocol/blob/main';

/** GitHub `tree/main` base — reuse for directory links (starters, examples). */
export const GITHUB_TREE_MAIN = 'https://github.com/asap-protocol/asap-protocol/tree/main';

/**
 * Build a canonical `blob/main` URL for a repo-relative path.
 *
 * Example: `githubBlobMain('docs/migration.md#anchor')`
 */
export function githubBlobMain(repoPath: string): string {
  const normalized = repoPath.replace(/^\//, '');
  return `${GITHUB_BLOB_MAIN}/${normalized}`;
}

/**
 * Build a canonical `tree/main` URL for a repo-relative directory path.
 *
 * Example: `githubTreeMain('examples/starters')`
 */
export function githubTreeMain(repoPath: string): string {
  const normalized = repoPath.replace(/^\//, '');
  return `${GITHUB_TREE_MAIN}/${normalized}`;
}

/** Build for agents guide used by homepage CTAs. */
export const BUILD_FOR_AGENTS_GUIDE_URL = githubBlobMain('docs/guides/build-for-agents.md');

/** Thin starter pack index. */
export const STARTERS_URL = githubTreeMain('examples/starters');

/** v2.5.4 migration note. Kept for the Dist Loop link contract. */
export const DOCS_MIGRATION_254_URL = githubBlobMain(
  'docs/migration.md#upgrading-from-v253-to-v254'
);

/** Changelog anchor for [2.5.4]. Kept for the Dist Loop link contract. */
export const CHANGELOG_254_URL = githubBlobMain('CHANGELOG.md#254---2026-07-18');

/** GitHub Release for v2.5.4. Kept for the Dist Loop link contract. */
export const RELEASE_254_URL = 'https://github.com/asap-protocol/asap-protocol/releases/tag/v2.5.4';

/**
 * Latest shipped library release shown on the homepage.
 * Bump with the PyPI tag. Anchors match `docs/index.md`.
 */
export const CURRENT_RELEASE_VERSION = '2.5.10';

/** Short label for {@link CURRENT_RELEASE_VERSION}. This patch is not Formal Spec. */
export const CURRENT_RELEASE_TAGLINE = 'Security & correctness';

/** v2.5.10 migration note. */
export const DOCS_MIGRATION_2510_URL = githubBlobMain(
  'docs/migration.md#upgrading-from-v259-to-v2510'
);

/** Changelog anchor for [2.5.10] (2026-10-23). */
export const CHANGELOG_2510_URL = githubBlobMain('CHANGELOG.md#2510---2026-10-23');

/** GitHub Release for v2.5.10. */
export const RELEASE_2510_URL =
  'https://github.com/asap-protocol/asap-protocol/releases/tag/v2.5.10';
