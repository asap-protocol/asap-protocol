/**
 * Stable homepage `data-cta` ids for Vercel analytics and `/api/telemetry`.
 * Keep landing components aligned with these constants to avoid dashboard drift.
 * Add new ids when CTAs are introduced; do not rename existing keys casually.
 */

export const HOMEPAGE_HERO_CTA_IDS = {
  /** Legacy release-tag badge id — retained for historical CTR dashboards. */
  releaseBadge: 'hero-release-badge',
  /** Hero badge for the Build for agents guide (not a version tag). */
  distLoopBadge: 'hero-dist-loop-badge',
  /** Primary CTA for the Build for agents guide. */
  buildForAgents: 'hero-build-for-agents',
  /** Primary CTA for examples/starters. */
  viewStarters: 'hero-view-starters',
  exploreAgents: 'hero-explore-agents',
  registerAgent: 'hero-register-agent',
} as const;

/** CTA ids used by the “What’s new” ribbon (subset also appears in {@link HOMEPAGE_CTA_IDS}). */
export const WHATS_NEW_RIBBON_CTA_IDS = {
  /** Build for agents guide. */
  docsBuildForAgents: 'docs-build-for-agents',
  /** Thin starter pack. */
  docsStarters: 'docs-starters',
  docsMcpAuthBridge: 'docs-mcp-auth-bridge',
  docsMcpIntegration: 'docs-mcp-integration',
  docsMcpAuthExample: 'docs-mcp-auth-example',
  docsMigration250: 'docs-migration-250', // retained for historical CTR dashboards
  /** v2.5.3 migration pill — retained for historical CTR dashboards. */
  docsMigration253: 'docs-migration-253',
  /** v2.5.4 migration pill — retained for historical CTR dashboards. */
  docsMigration254: 'docs-migration-254',
  /** v2.5.10 migration pill (WhatsNewRibbon). */
  docsMigration2510: 'docs-migration-2510',
  docsOpenapi: 'docs-openapi',
  docsTypescript: 'docs-typescript',
  docsWorkflowConnectors: 'docs-workflow-connectors',
  /** Runnable sample under examples/workflow_asap_connector (feature DocsLink). */
  docsWorkflowConnectorExample: 'docs-workflow-connector-example',
  docsAutomationConnectorSecurity: 'docs-automation-connector-security',
  docsMicrosoftAgentFramework: 'docs-microsoft-agent-framework',
  docsNemoAgentToolkit: 'docs-nemo-agent-toolkit',
  docsMastraIntegration: 'docs-mastra-integration',
  docsOpenaiAgentsIntegration: 'docs-openai-agents-integration',
  docsAutoRegistration: 'docs-auto-registration',
  featurePerAgentIdentity: 'feature-per-agent-identity',
  featureScopedCapabilities: 'feature-scoped-capabilities',
  docsCapabilitiesEscalation: 'docs-capabilities-escalation',
  /** Generic changelog pill id — retained for historical CTR dashboards. */
  releaseChangelogGithub: 'release-changelog-github',
  /** v2.5.4 changelog pill — retained for historical CTR dashboards. */
  releaseChangelog254: 'release-changelog-254',
  /** v2.5.10 changelog pill (WhatsNewRibbon). */
  releaseChangelog2510: 'release-changelog-2510',
} as const;

/** How it Works section primary CTAs (guide + starters). */
export const HOW_IT_WORKS_CTA_IDS = {
  buildForAgents: 'how-it-works-build-for-agents',
  viewStarters: 'how-it-works-view-starters',
} as const;

/** Lab II docs CTAs on `/developer-experience` (reuse ribbon ids where shared). */
export const DEVELOPER_EXPERIENCE_CTA_IDS = {
  docsWorkflowConnectors: WHATS_NEW_RIBBON_CTA_IDS.docsWorkflowConnectors,
  docsAutomationConnectorSecurity: WHATS_NEW_RIBBON_CTA_IDS.docsAutomationConnectorSecurity,
  docsMicrosoftAgentFramework: WHATS_NEW_RIBBON_CTA_IDS.docsMicrosoftAgentFramework,
  docsNemoAgentToolkit: WHATS_NEW_RIBBON_CTA_IDS.docsNemoAgentToolkit,
} as const;

/** Slugs from `/features/[slug]` cards — order matches the homepage grid. */
export const LANDING_FEATURE_SLUGS = [
  'openapi-adapter',
  'workflow-connectors',
  'automation-connector-security',
  'typescript-sdk',
  'mastra-adapter',
  'openai-agents-adapter',
  'auto-registration',
  'lite-registry',
  'verified-trust',
  '1-click-integration',
  'full-observability',
  'per-agent-identity',
  'scoped-capabilities',
  'streaming-responses',
] as const;

/** Ordered shell keys returned by `/api/telemetry` (`ctr_per_cta`). */
export const HOMEPAGE_CTA_IDS = [
  HOMEPAGE_HERO_CTA_IDS.releaseBadge,
  HOMEPAGE_HERO_CTA_IDS.distLoopBadge,
  HOMEPAGE_HERO_CTA_IDS.buildForAgents,
  HOMEPAGE_HERO_CTA_IDS.viewStarters,
  HOMEPAGE_HERO_CTA_IDS.exploreAgents,
  HOMEPAGE_HERO_CTA_IDS.registerAgent,
  WHATS_NEW_RIBBON_CTA_IDS.docsBuildForAgents,
  WHATS_NEW_RIBBON_CTA_IDS.docsStarters,
  WHATS_NEW_RIBBON_CTA_IDS.docsMcpAuthBridge,
  WHATS_NEW_RIBBON_CTA_IDS.docsMcpIntegration,
  WHATS_NEW_RIBBON_CTA_IDS.docsMcpAuthExample,
  WHATS_NEW_RIBBON_CTA_IDS.docsMigration253,
  WHATS_NEW_RIBBON_CTA_IDS.docsMigration254,
  WHATS_NEW_RIBBON_CTA_IDS.docsTypescript,
  WHATS_NEW_RIBBON_CTA_IDS.docsWorkflowConnectors,
  WHATS_NEW_RIBBON_CTA_IDS.docsAutomationConnectorSecurity,
  WHATS_NEW_RIBBON_CTA_IDS.docsMicrosoftAgentFramework,
  WHATS_NEW_RIBBON_CTA_IDS.docsNemoAgentToolkit,
  ...LANDING_FEATURE_SLUGS.map((slug) => `feature-${slug}` as const),
  HOW_IT_WORKS_CTA_IDS.buildForAgents,
  HOW_IT_WORKS_CTA_IDS.viewStarters,
  WHATS_NEW_RIBBON_CTA_IDS.releaseChangelogGithub,
  WHATS_NEW_RIBBON_CTA_IDS.releaseChangelog254,
  // Appended so earlier homepage_cta_ids indexes stay stable.
  WHATS_NEW_RIBBON_CTA_IDS.docsMigration2510,
  WHATS_NEW_RIBBON_CTA_IDS.releaseChangelog2510,
] as const;
