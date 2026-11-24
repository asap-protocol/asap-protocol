import { describe, expect, it } from 'vitest';

import {
  BUILD_FOR_AGENTS_GUIDE_URL,
  CHANGELOG_2510_URL,
  CHANGELOG_2511_URL,
  CHANGELOG_254_URL,
  CURRENT_RELEASE_TAGLINE,
  CURRENT_RELEASE_VERSION,
  DOCS_MIGRATION_2510_URL,
  DOCS_MIGRATION_2511_URL,
  DOCS_MIGRATION_254_URL,
  GITHUB_BLOB_MAIN,
  GITHUB_TREE_MAIN,
  RELEASE_2510_URL,
  RELEASE_2511_URL,
  RELEASE_254_URL,
  STARTERS_URL,
  githubBlobMain,
  githubTreeMain,
  interopReleaseLinks,
} from '@/lib/landing/dist-loop-links';

const ORG_REPO = 'asap-protocol/asap-protocol';
const CANONICAL_PREFIX = `https://github.com/${ORG_REPO}`;

describe('dist-loop-links org cutover contract', () => {
  const exportedUrls = [
    GITHUB_BLOB_MAIN,
    GITHUB_TREE_MAIN,
    BUILD_FOR_AGENTS_GUIDE_URL,
    STARTERS_URL,
    DOCS_MIGRATION_254_URL,
    CHANGELOG_254_URL,
    RELEASE_254_URL,
    DOCS_MIGRATION_2510_URL,
    CHANGELOG_2510_URL,
    RELEASE_2510_URL,
    DOCS_MIGRATION_2511_URL,
    CHANGELOG_2511_URL,
    RELEASE_2511_URL,
    ...Object.values(interopReleaseLinks({})),
  ];

  it('keeps blob/tree/release bases on the asap-protocol org', () => {
    expect(GITHUB_BLOB_MAIN).toBe(`${CANONICAL_PREFIX}/blob/main`);
    expect(GITHUB_TREE_MAIN).toBe(`${CANONICAL_PREFIX}/tree/main`);
    expect(RELEASE_254_URL).toBe(`${CANONICAL_PREFIX}/releases/tag/v2.5.4`);
  });

  it('never exports the former personal-fork owner', () => {
    for (const url of exportedUrls) {
      expect(url).not.toContain('adriannoes');
      expect(url).toContain(ORG_REPO);
    }
  });

  it('builds exact blob/tree destinations including leading-slash and anchors', () => {
    expect(githubBlobMain('/docs/migration.md#anchor')).toBe(
      `${CANONICAL_PREFIX}/blob/main/docs/migration.md#anchor`
    );
    expect(githubTreeMain('/examples/starters')).toBe(
      `${CANONICAL_PREFIX}/tree/main/examples/starters`
    );
  });

  it('pins Dist Loop CTA constants to post-merge main paths', () => {
    expect(BUILD_FOR_AGENTS_GUIDE_URL).toBe(
      `${CANONICAL_PREFIX}/blob/main/docs/guides/build-for-agents.md`
    );
    expect(STARTERS_URL).toBe(`${CANONICAL_PREFIX}/tree/main/examples/starters`);
    expect(DOCS_MIGRATION_254_URL).toBe(
      `${CANONICAL_PREFIX}/blob/main/docs/migration.md#upgrading-from-v253-to-v254`
    );
    expect(CHANGELOG_254_URL).toBe(`${CANONICAL_PREFIX}/blob/main/CHANGELOG.md#254---2026-07-18`);
  });

  it('keeps the v2.5.10 release URLs for historical dashboards', () => {
    expect(RELEASE_2510_URL).toBe(`${CANONICAL_PREFIX}/releases/tag/v2.5.10`);
    expect(DOCS_MIGRATION_2510_URL).toBe(
      `${CANONICAL_PREFIX}/blob/main/docs/migration.md#upgrading-from-v259-to-v2510`
    );
    expect(CHANGELOG_2510_URL).toBe(`${CANONICAL_PREFIX}/blob/main/CHANGELOG.md#2510---2026-10-23`);
  });

  it('keeps the v2.5.11 release URLs for historical dashboards', () => {
    expect(RELEASE_2511_URL).toBe(`${CANONICAL_PREFIX}/releases/tag/v2.5.11`);
    expect(DOCS_MIGRATION_2511_URL).toBe(
      `${CANONICAL_PREFIX}/blob/main/docs/migration.md#upgrading-from-v2510-to-v2511`
    );
    expect(CHANGELOG_2511_URL).toBe(`${CANONICAL_PREFIX}/blob/main/CHANGELOG.md#2511---2026-11-09`);
  });

  it('links the Interop announcement to the release docs on main in production', () => {
    expect(CURRENT_RELEASE_VERSION).toBe('2.6.0');
    expect(CURRENT_RELEASE_TAGLINE).toBe('Interop');
    expect(interopReleaseLinks({ VERCEL_ENV: 'production' })).toEqual({
      guide: `${CANONICAL_PREFIX}/blob/main/docs/guides/interoperability.md`,
      migration: `${CANONICAL_PREFIX}/blob/main/docs/migration.md#upgrading-from-v25x-to-v260`,
      changelog: `${CANONICAL_PREFIX}/blob/main/CHANGELOG.md#260`,
    });
  });

  it('links a preview to the docs in the deployed commit', () => {
    const commit = 'a'.repeat(40);
    expect(interopReleaseLinks({ VERCEL_ENV: 'preview', VERCEL_GIT_COMMIT_SHA: commit })).toEqual({
      guide: `${CANONICAL_PREFIX}/blob/${commit}/docs/guides/interoperability.md`,
      migration: `${CANONICAL_PREFIX}/blob/${commit}/docs/migration.md#upgrading-from-v25x-to-v260`,
      changelog: `${CANONICAL_PREFIX}/blob/${commit}/CHANGELOG.md#260`,
    });
  });

  it('uses main when preview commit metadata is missing or malformed', () => {
    expect(interopReleaseLinks({ VERCEL_ENV: 'preview' })).toEqual(interopReleaseLinks({}));
    expect(
      interopReleaseLinks({ VERCEL_ENV: 'preview', VERCEL_GIT_COMMIT_SHA: '../other' })
    ).toEqual(interopReleaseLinks({}));
  });
});
