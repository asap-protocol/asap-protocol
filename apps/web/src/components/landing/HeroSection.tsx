import { AnimatedText } from '@/components/ui/animated-text';
import { Button } from '@/components/ui/button';
import { BackgroundPaths } from '@/components/ui/background-paths';
import { DistLoopPrimaryCtas } from '@/components/landing/DistLoopPrimaryCtas';
import { HeroTerminal } from '@/components/landing/HeroTerminal';
import Link from 'next/link';
import { HOMEPAGE_HERO_CTA_IDS } from '@/lib/telemetry/homepage-cta-ids';
import {
  CURRENT_RELEASE_TAGLINE,
  CURRENT_RELEASE_URL,
  CURRENT_RELEASE_VERSION,
} from '@/lib/landing/dist-loop-links';
import { EXTERNAL_LINK_FOCUS_CLASS, OpensInNewTabHint } from '@/components/links/opens-in-new-tab';
import { cn } from '@/lib/utils';

export function HeroSection() {
  return (
    <section className="relative flex min-h-[90vh] w-full flex-col items-center justify-center overflow-hidden bg-zinc-950 py-24 lg:py-32">
      <BackgroundPaths />
      <div className="pointer-events-none absolute top-1/2 left-1/2 h-[800px] w-[800px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-indigo-500/10 blur-[120px]" />

      <div className="relative z-10 container mx-auto px-4 md:px-6">
        <div className="grid items-center gap-12 lg:grid-cols-2 lg:gap-8">
          <div className="animate-in fade-in slide-in-from-bottom-5 flex flex-col justify-center space-y-8 text-center duration-700 ease-out lg:text-left">
            <div className="space-y-4">
              <Link
                href={CURRENT_RELEASE_URL}
                target="_blank"
                rel="noopener noreferrer"
                data-cta={HOMEPAGE_HERO_CTA_IDS.releaseBadge2510}
                aria-label={`View ASAP Protocol v${CURRENT_RELEASE_VERSION} release on GitHub`}
                className={cn('inline-flex', EXTERNAL_LINK_FOCUS_CLASS)}
              >
                <div className="inline-flex items-center rounded-full border border-indigo-500/30 bg-indigo-500/10 px-3 py-1 text-sm font-medium text-indigo-300 backdrop-blur-sm transition-colors hover:border-indigo-500/60 hover:bg-indigo-500/15">
                  <span className="mr-2 flex h-2 w-2 animate-pulse rounded-full bg-indigo-500"></span>
                  v{CURRENT_RELEASE_VERSION} — {CURRENT_RELEASE_TAGLINE}
                  <OpensInNewTabHint />
                </div>
              </Link>
              <AnimatedText
                text="The next users of software are agents"
                className="bg-gradient-to-r from-white to-white/80 bg-clip-text text-4xl leading-tight font-bold tracking-tighter text-transparent sm:text-5xl xl:text-6xl/none"
              />
              <AnimatedText
                text="ASAP gives them the machine-readable foundation they need: discoverable capabilities, scoped identity, compliance checks, and SDKs that turn existing APIs into agent-ready interfaces."
                as="p"
                delay={0.3}
                className="mx-auto max-w-[600px] text-zinc-400 md:text-xl lg:mx-0"
              />
            </div>

            <div className="flex flex-col justify-center gap-4 min-[400px]:flex-row min-[400px]:flex-wrap lg:justify-start">
              <DistLoopPrimaryCtas
                buildForAgentsCtaId={HOMEPAGE_HERO_CTA_IDS.buildForAgents}
                viewStartersCtaId={HOMEPAGE_HERO_CTA_IDS.viewStarters}
                className="contents"
              />
              <Button
                asChild
                size="lg"
                variant="outline"
                className="w-full border-zinc-800 text-zinc-300 hover:bg-zinc-900 hover:text-white min-[400px]:w-auto"
              >
                <Link href="/browse" data-cta={HOMEPAGE_HERO_CTA_IDS.exploreAgents}>
                  Explore Agents
                </Link>
              </Button>
              <Button
                asChild
                size="lg"
                variant="outline"
                className="w-full border-zinc-800 text-zinc-300 hover:bg-zinc-900 hover:text-white min-[400px]:w-auto"
              >
                <Link href="/dashboard/register" data-cta={HOMEPAGE_HERO_CTA_IDS.registerAgent}>
                  Register Agent
                </Link>
              </Button>
            </div>
          </div>

          <HeroTerminal />
        </div>
      </div>
    </section>
  );
}
