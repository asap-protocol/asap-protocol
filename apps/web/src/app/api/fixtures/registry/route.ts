import { NextRequest, NextResponse } from 'next/server';

import { FixtureRegistryQuerySchema, parseSearchParams } from '@/lib/api-schemas';
import { asapVersionForFixtureIndex } from '@/lib/protocol-versions';

function buildFixtureAgents(count: number): Record<string, unknown>[] {
  const agents: Record<string, unknown>[] = [];
  const skillsPool = ['code_review', 'summarize', 'translate', 'search', 'analyze', 'synthesize'];
  for (let i = 0; i < count; i++) {
    const id = `urn:asap:agent:loadtest:agent-${i}`;
    const name = `Load Test Agent ${i}`;
    agents.push({
      id,
      name,
      description: `Fixture agent ${i} for client-side load testing. Simulates registry.json payload.`,
      endpoints: {
        asap: `https://example.com/agents/${i}/asap`,
        http: `https://example.com/agents/${i}/asap`,
      },
      skills: skillsPool.slice(0, 2 + (i % 3)),
      asap_version: asapVersionForFixtureIndex(i),
      verification: i % 5 === 0 ? { status: 'verified' } : null,
      online_check: false,
    });
  }
  return agents;
}

export async function GET(request: NextRequest) {
  if (process.env.ENABLE_FIXTURE_ROUTES !== 'true') {
    return NextResponse.json({ error: 'Not available' }, { status: 404 });
  }

  const parsed = parseSearchParams(FixtureRegistryQuerySchema, request.nextUrl.searchParams);
  if (!parsed.success) {
    return NextResponse.json({ error: parsed.error }, { status: 400 });
  }

  const agents = buildFixtureAgents(parsed.data.count);
  return NextResponse.json(agents, {
    headers: { 'Cache-Control': 'no-store' },
  });
}
