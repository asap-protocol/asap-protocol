import { describe, expect, it } from 'vitest';

import {
  BUILD_FOR_AGENTS_GUIDE_URL,
  CHANGELOG_2510_URL,
  CHANGELOG_254_URL,
  CURRENT_CHANGELOG_URL,
  CURRENT_MIGRATION_URL,
  CURRENT_RELEASE_DETAIL,
  CURRENT_RELEASE_TAGLINE,
  CURRENT_RELEASE_URL,
  CURRENT_RELEASE_VERSION,
  DOCS_MIGRATION_2510_URL,
  DOCS_MIGRATION_254_URL,
  GITHUB_BLOB_MAIN,
  GITHUB_TREE_MAIN,
  RELEASE_2510_URL,
  RELEASE_254_URL,
  STARTERS_URL,
  githubBlobMain,
  githubTreeMain,
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
    CURRENT_RELEASE_URL,
    CURRENT_MIGRATION_URL,
    CURRENT_CHANGELOG_URL,
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

  it('pins homepage release CTAs to the shipped v2.5.10 tag', () => {
    const versionSlug = CURRENT_RELEASE_VERSION.replaceAll('.', '');
    expect(CURRENT_RELEASE_VERSION).toBe('2.5.10');
    expect(CURRENT_RELEASE_TAGLINE).toBe('Security & correctness');
    expect(CURRENT_RELEASE_DETAIL).toBe('delegation JWTs');
    expect(RELEASE_2510_URL).toBe(`${CANONICAL_PREFIX}/releases/tag/v2.5.10`);
    expect(CURRENT_RELEASE_URL).toBe(RELEASE_2510_URL);
    expect(CURRENT_RELEASE_URL).toContain(`v${CURRENT_RELEASE_VERSION}`);
    expect(DOCS_MIGRATION_2510_URL).toBe(
      `${CANONICAL_PREFIX}/blob/main/docs/migration.md#upgrading-from-v259-to-v2510`
    );
    expect(CURRENT_MIGRATION_URL).toBe(DOCS_MIGRATION_2510_URL);
    expect(CURRENT_MIGRATION_URL).toContain(versionSlug);
    expect(CHANGELOG_2510_URL).toBe(`${CANONICAL_PREFIX}/blob/main/CHANGELOG.md#2510---2026-10-23`);
    expect(CURRENT_CHANGELOG_URL).toBe(CHANGELOG_2510_URL);
    expect(CURRENT_CHANGELOG_URL).toContain(versionSlug);
  });
});
