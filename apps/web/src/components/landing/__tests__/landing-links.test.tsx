import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { DistLoopPrimaryCtas } from '@/components/landing/DistLoopPrimaryCtas';
import { FeaturesSection } from '@/components/landing/FeaturesSection';
import { HeroSection } from '@/components/landing/HeroSection';
import { WhatsNewRibbon } from '@/components/landing/WhatsNewRibbon';
import {
  BUILD_FOR_AGENTS_GUIDE_URL,
  CHANGELOG_2510_URL,
  DOCS_MIGRATION_2510_URL,
  RELEASE_2510_URL,
  STARTERS_URL,
} from '@/lib/landing/dist-loop-links';

vi.mock('@/components/ui/background-paths', () => ({
  BackgroundPaths: () => null,
}));

function linkByCta(container: HTMLElement, cta: string): HTMLElement {
  const link = container.querySelector(`[data-cta="${cta}"]`);
  if (!(link instanceof HTMLElement)) {
    throw new Error(`Expected link with data-cta="${cta}"`);
  }
  return link;
}

describe('Dist Loop landing link contract', () => {
  it('wires DistLoopPrimaryCtas to guide/starters with safe external attrs', () => {
    render(
      <DistLoopPrimaryCtas
        buildForAgentsCtaId="hero-build-for-agents"
        viewStartersCtaId="hero-view-starters"
      />
    );

    const guide = screen.getByRole('link', { name: /build for agents/i });
    expect(guide).toHaveAttribute('href', BUILD_FOR_AGENTS_GUIDE_URL);
    expect(guide).toHaveAttribute('data-cta', 'hero-build-for-agents');
    expect(guide).toHaveAttribute('target', '_blank');
    expect(guide).toHaveAttribute('rel', 'noopener noreferrer');

    const starters = screen.getByRole('link', { name: /view starters/i });
    expect(starters).toHaveAttribute('href', STARTERS_URL);
    expect(starters).toHaveAttribute('data-cta', 'hero-view-starters');
    expect(starters).toHaveAttribute('target', '_blank');
    expect(starters).toHaveAttribute('rel', 'noopener noreferrer');
  });

  it('points the hero badge at the v2.5.10 GitHub release', () => {
    render(<HeroSection />);

    const badge = screen.getByRole('link', {
      name: /view asap protocol v2\.5\.10 release on github/i,
    });
    expect(badge).toHaveAttribute('href', RELEASE_2510_URL);
    expect(badge).toHaveAttribute('data-cta', 'hero-dist-loop-badge');
    expect(badge).toHaveTextContent('v2.5.10 — Security & correctness');
  });

  it('keeps WhatsNewRibbon docs pills on canonical GitHub URLs for v2.5.10', () => {
    render(<WhatsNewRibbon />);

    const ribbon = screen.getByRole('complementary', {
      name: /what's new in asap protocol v2\.5\.10/i,
    });

    expect(ribbon).toHaveTextContent('Security & correctness · delegation JWTs');
    expect(linkByCta(ribbon, 'docs-migration-2510')).toHaveAttribute(
      'href',
      DOCS_MIGRATION_2510_URL
    );
    expect(linkByCta(ribbon, 'release-changelog-2510')).toHaveAttribute('href', CHANGELOG_2510_URL);
    expect(linkByCta(ribbon, 'docs-build-for-agents')).toHaveAttribute(
      'href',
      BUILD_FOR_AGENTS_GUIDE_URL
    );
    expect(linkByCta(ribbon, 'docs-starters')).toHaveAttribute('href', STARTERS_URL);
  });

  it('routes FeaturesSection cards to external GitHub docs, not /features/*', () => {
    render(<FeaturesSection />);

    const featureLinks = screen
      .getAllByRole('link')
      .filter((link) => link.getAttribute('data-cta')?.startsWith('feature-'));

    expect(featureLinks.length).toBeGreaterThan(0);
    for (const link of featureLinks) {
      const href = link.getAttribute('href') ?? '';
      expect(href).toMatch(/^https:\/\/github\.com\/asap-protocol\/asap-protocol\/blob\/main\//);
      expect(href).not.toMatch(/^\/features\//);
      expect(link).toHaveAttribute('target', '_blank');
      expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    }
  });
});
