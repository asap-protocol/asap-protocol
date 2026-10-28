import { describe, expect, it } from 'vitest';

import {
  HOMEPAGE_CTA_IDS,
  HOMEPAGE_HERO_CTA_IDS,
  HOW_IT_WORKS_CTA_IDS,
  WHATS_NEW_RIBBON_CTA_IDS,
} from '@/lib/telemetry/homepage-cta-ids';

describe('homepage-cta-ids Dist Loop extensions', () => {
  it('keeps legacy hero marketplace ids stable', () => {
    expect(HOMEPAGE_HERO_CTA_IDS.exploreAgents).toBe('hero-explore-agents');
    expect(HOMEPAGE_HERO_CTA_IDS.registerAgent).toBe('hero-register-agent');
    expect(HOMEPAGE_HERO_CTA_IDS.releaseBadge).toBe('hero-release-badge');
  });

  it('adds primary Dist Loop hero and section ids', () => {
    expect(HOMEPAGE_HERO_CTA_IDS.distLoopBadge).toBe('hero-dist-loop-badge');
    expect(HOMEPAGE_HERO_CTA_IDS.releaseBadge2510).toBe('hero-release-2510');
    expect(HOMEPAGE_HERO_CTA_IDS.buildForAgents).toBe('hero-build-for-agents');
    expect(HOMEPAGE_HERO_CTA_IDS.viewStarters).toBe('hero-view-starters');
    expect(WHATS_NEW_RIBBON_CTA_IDS.docsBuildForAgents).toBe('docs-build-for-agents');
    expect(WHATS_NEW_RIBBON_CTA_IDS.docsStarters).toBe('docs-starters');
    expect(WHATS_NEW_RIBBON_CTA_IDS.docsMigration254).toBe('docs-migration-254');
    expect(WHATS_NEW_RIBBON_CTA_IDS.docsMigration2510).toBe('docs-migration-2510');
    expect(WHATS_NEW_RIBBON_CTA_IDS.releaseChangelog254).toBe('release-changelog-254');
    expect(WHATS_NEW_RIBBON_CTA_IDS.releaseChangelog2510).toBe('release-changelog-2510');
    expect(HOW_IT_WORKS_CTA_IDS.buildForAgents).toBe('how-it-works-build-for-agents');
    expect(HOW_IT_WORKS_CTA_IDS.viewStarters).toBe('how-it-works-view-starters');
  });

  it('includes new Dist Loop ids in the /api/telemetry shell list', () => {
    const shell = new Set<string>(HOMEPAGE_CTA_IDS);
    expect(shell.has('hero-dist-loop-badge')).toBe(true);
    expect(shell.has('hero-release-2510')).toBe(true);
    expect(shell.has('hero-build-for-agents')).toBe(true);
    expect(shell.has('hero-view-starters')).toBe(true);
    expect(shell.has('docs-build-for-agents')).toBe(true);
    expect(shell.has('docs-starters')).toBe(true);
    expect(shell.has('docs-migration-254')).toBe(true);
    expect(shell.has('docs-migration-2510')).toBe(true);
    expect(shell.has('release-changelog-254')).toBe(true);
    expect(shell.has('release-changelog-2510')).toBe(true);
    expect(shell.has('how-it-works-build-for-agents')).toBe(true);
    expect(shell.has('how-it-works-view-starters')).toBe(true);
    // Legacy ids must remain for dashboard continuity.
    expect(shell.has('hero-release-badge')).toBe(true);
    expect(shell.has('hero-explore-agents')).toBe(true);
    expect(shell.has('hero-register-agent')).toBe(true);
    expect(shell.has('docs-migration-253')).toBe(true);
    expect(shell.has('release-changelog-github')).toBe(true);
  });

  it('keeps earlier homepage_cta_ids indexes and appends the v2.5.10 ids', () => {
    expect([...HOMEPAGE_CTA_IDS]).toEqual([
      'hero-release-badge',
      'hero-dist-loop-badge',
      'hero-build-for-agents',
      'hero-view-starters',
      'hero-explore-agents',
      'hero-register-agent',
      'docs-build-for-agents',
      'docs-starters',
      'docs-mcp-auth-bridge',
      'docs-mcp-integration',
      'docs-mcp-auth-example',
      'docs-migration-253',
      'docs-migration-254',
      'docs-typescript',
      'docs-workflow-connectors',
      'docs-automation-connector-security',
      'docs-microsoft-agent-framework',
      'docs-nemo-agent-toolkit',
      'feature-openapi-adapter',
      'feature-workflow-connectors',
      'feature-automation-connector-security',
      'feature-typescript-sdk',
      'feature-mastra-adapter',
      'feature-openai-agents-adapter',
      'feature-auto-registration',
      'feature-lite-registry',
      'feature-verified-trust',
      'feature-1-click-integration',
      'feature-full-observability',
      'feature-per-agent-identity',
      'feature-scoped-capabilities',
      'feature-streaming-responses',
      'how-it-works-build-for-agents',
      'how-it-works-view-starters',
      'release-changelog-github',
      'release-changelog-254',
      'docs-migration-2510',
      'release-changelog-2510',
      'hero-release-2510',
    ]);
  });
});
