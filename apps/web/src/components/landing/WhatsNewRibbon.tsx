import Link from 'next/link';
import { WHATS_NEW_RIBBON_CTA_IDS } from '@/lib/telemetry/homepage-cta-ids';
import {
  BUILD_FOR_AGENTS_GUIDE_URL,
  CURRENT_CHANGELOG_URL,
  CURRENT_MIGRATION_URL,
  CURRENT_RELEASE_DETAIL,
  CURRENT_RELEASE_TAGLINE,
  CURRENT_RELEASE_VERSION,
  GITHUB_BLOB_MAIN,
  GITHUB_TREE_MAIN,
  STARTERS_URL,
} from '@/lib/landing/dist-loop-links';
import { EXTERNAL_LINK_FOCUS_CLASS, OpensInNewTabHint } from '@/components/links/opens-in-new-tab';
import { cn } from '@/lib/utils';
import {
  Fingerprint,
  KeySquare,
  GitBranch,
  ShieldCheck,
  Sparkles,
  ArrowUpRight,
  Code,
  BookOpen,
  Workflow,
  Lock,
  Beaker,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

type Pill = {
  label: string;
  href: string;
  icon: LucideIcon;
  external?: boolean;
  /** Stable id for analytics (Vercel / site→docs CTR). */
  dataCta: string;
};

const DOCS_MCP_AUTH_BRIDGE = `${GITHUB_BLOB_MAIN}/docs/adapters/mcp-auth-bridge.md`;
const EXAMPLE_MCP_AUTH_BRIDGE = `${GITHUB_TREE_MAIN}/examples/mcp_auth_bridge`;
const DOCS_TS_SDK = `${GITHUB_BLOB_MAIN}/docs/sdks/typescript.md`;
// Lab II docs ship with v2.5.3.
const DOCS_WORKFLOW_CONNECTORS = `${GITHUB_BLOB_MAIN}/docs/integrations/workflow-connectors.md`;
const DOCS_AUTOMATION_CONNECTOR_SECURITY = `${GITHUB_BLOB_MAIN}/docs/guides/automation-connector-security.md`;
const DOCS_MICROSOFT_AGENT_FRAMEWORK = `${GITHUB_BLOB_MAIN}/docs/integrations/microsoft-agent-framework.md`;
const DOCS_NEMO_AGENT_TOOLKIT = `${GITHUB_BLOB_MAIN}/docs/integrations/nemo-agent-toolkit.md`;

const PILLS: Pill[] = [
  {
    label: 'Build for agents',
    href: BUILD_FOR_AGENTS_GUIDE_URL,
    icon: BookOpen,
    external: true,
    dataCta: WHATS_NEW_RIBBON_CTA_IDS.docsBuildForAgents,
  },
  {
    label: 'Starters',
    href: STARTERS_URL,
    icon: Sparkles,
    external: true,
    dataCta: WHATS_NEW_RIBBON_CTA_IDS.docsStarters,
  },
  {
    label: 'Workflow connectors',
    href: DOCS_WORKFLOW_CONNECTORS,
    icon: Workflow,
    external: true,
    dataCta: WHATS_NEW_RIBBON_CTA_IDS.docsWorkflowConnectors,
  },
  {
    label: 'Connector security',
    href: DOCS_AUTOMATION_CONNECTOR_SECURITY,
    icon: Lock,
    external: true,
    dataCta: WHATS_NEW_RIBBON_CTA_IDS.docsAutomationConnectorSecurity,
  },
  {
    label: 'Migration',
    href: CURRENT_MIGRATION_URL,
    icon: ShieldCheck,
    external: true,
    dataCta: WHATS_NEW_RIBBON_CTA_IDS.docsMigration2510,
  },
  {
    label: 'MCP Auth Bridge',
    href: DOCS_MCP_AUTH_BRIDGE,
    icon: KeySquare,
    external: true,
    dataCta: WHATS_NEW_RIBBON_CTA_IDS.docsMcpAuthBridge,
  },
  {
    label: 'Example',
    href: EXAMPLE_MCP_AUTH_BRIDGE,
    icon: Sparkles,
    external: true,
    dataCta: WHATS_NEW_RIBBON_CTA_IDS.docsMcpAuthExample,
  },
  {
    label: 'MAF (research)',
    href: DOCS_MICROSOFT_AGENT_FRAMEWORK,
    icon: BookOpen,
    external: true,
    dataCta: WHATS_NEW_RIBBON_CTA_IDS.docsMicrosoftAgentFramework,
  },
  {
    label: 'NeMo (experimental)',
    href: DOCS_NEMO_AGENT_TOOLKIT,
    icon: Beaker,
    external: true,
    dataCta: WHATS_NEW_RIBBON_CTA_IDS.docsNemoAgentToolkit,
  },
  {
    label: 'Identity',
    href: '/features/per-agent-identity',
    icon: Fingerprint,
    dataCta: WHATS_NEW_RIBBON_CTA_IDS.featurePerAgentIdentity,
  },
  {
    label: 'Capabilities',
    href: '/features/scoped-capabilities',
    icon: KeySquare,
    dataCta: WHATS_NEW_RIBBON_CTA_IDS.featureScopedCapabilities,
  },
  {
    label: 'TypeScript SDK',
    href: DOCS_TS_SDK,
    icon: Code,
    external: true,
    dataCta: WHATS_NEW_RIBBON_CTA_IDS.docsTypescript,
  },
  {
    label: 'Changelog',
    href: CURRENT_CHANGELOG_URL,
    icon: GitBranch,
    external: true,
    dataCta: WHATS_NEW_RIBBON_CTA_IDS.releaseChangelog2510,
  },
];

export function WhatsNewRibbon() {
  return (
    <aside
      aria-label={`Latest release v${CURRENT_RELEASE_VERSION}`}
      className="w-full border-y border-zinc-900 bg-zinc-950"
    >
      <div className="container mx-auto flex max-w-5xl flex-col gap-4 px-4 py-8 md:flex-row md:items-center md:gap-4 md:px-6 md:py-10">
        <div className="flex items-center gap-2 md:shrink-0 md:border-r md:border-zinc-800 md:pr-4">
          <Sparkles className="h-4 w-4 shrink-0 text-indigo-400" aria-hidden />
          <div className="flex flex-col">
            <span className="font-mono text-xs tracking-wider text-indigo-400 uppercase">
              Latest release v{CURRENT_RELEASE_VERSION}
            </span>
            <span className="text-xs text-zinc-500">
              {CURRENT_RELEASE_TAGLINE} · {CURRENT_RELEASE_DETAIL}
            </span>
          </div>
        </div>

        <ul className="flex flex-wrap items-center gap-2">
          {PILLS.map((pill) => {
            const Icon = pill.icon;
            const linkProps = pill.external
              ? { target: '_blank', rel: 'noopener noreferrer' as const }
              : {};
            return (
              <li key={pill.label}>
                <Link
                  href={pill.href}
                  data-cta={pill.dataCta}
                  {...linkProps}
                  className={cn(
                    'group inline-flex items-center gap-1.5 rounded-full border border-zinc-800 bg-zinc-900/50 px-3 py-1.5 text-xs font-medium text-zinc-400 backdrop-blur-sm transition-colors hover:border-indigo-500/40 hover:bg-indigo-500/10 hover:text-indigo-300',
                    pill.external && EXTERNAL_LINK_FOCUS_CLASS
                  )}
                >
                  <Icon
                    className="h-3.5 w-3.5 text-zinc-500 transition-colors group-hover:text-indigo-400"
                    aria-hidden
                  />
                  <span>{pill.label}</span>
                  {pill.external ? (
                    <>
                      <OpensInNewTabHint />
                      <ArrowUpRight
                        className="h-3 w-3 text-zinc-600 transition-colors group-hover:text-indigo-400"
                        aria-hidden
                      />
                    </>
                  ) : null}
                </Link>
              </li>
            );
          })}
        </ul>
      </div>
    </aside>
  );
}
