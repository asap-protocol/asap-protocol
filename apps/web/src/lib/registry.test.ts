import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  REGISTRY_REVALIDATE_SECONDS,
  fetchRegistry,
  fetchRevokedUrns,
  resolveRegistryFetchUrl,
} from '@/lib/registry';

describe('resolveRegistryFetchUrl', () => {
  beforeEach(() => {
    vi.unstubAllEnvs();
    vi.stubEnv('NODE_ENV', 'development');
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it('uses GitHub default when REGISTRY_URL is unset', () => {
    expect(resolveRegistryFetchUrl()).toBe(
      'https://raw.githubusercontent.com/asap-protocol/asap-protocol/main/registry.json'
    );
  });

  it('rewrites localhost registry.json to process.env.PORT in development', () => {
    vi.stubEnv('REGISTRY_URL', 'http://127.0.0.1:3000/registry.json');
    vi.stubEnv('PORT', '3010');
    expect(resolveRegistryFetchUrl()).toBe('http://127.0.0.1:3010/registry.json');
  });

  it('does not rewrite remote REGISTRY_URL', () => {
    vi.stubEnv('REGISTRY_URL', 'https://example.com/registry.json');
    vi.stubEnv('PORT', '3010');
    expect(resolveRegistryFetchUrl()).toBe('https://example.com/registry.json');
  });

  it('does not rewrite in production', () => {
    vi.stubEnv('NODE_ENV', 'production');
    vi.stubEnv('REGISTRY_URL', 'http://127.0.0.1:3000/registry.json');
    vi.stubEnv('PORT', '3010');
    expect(resolveRegistryFetchUrl()).toBe('http://127.0.0.1:3000/registry.json');
  });
});

const REMOTE_REGISTRY_URL = 'https://example.com/registry.json';
const REMOTE_REVOKED_URL = 'https://example.com/revoked_agents.json';

function registryAgent(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: 'urn:asap:agent:seed:agent-0',
    name: 'Global Reviewer',
    description: 'Professional code review assistant for autonomous workflows.',
    endpoints: { http: 'https://example.com/seed/0/asap' },
    skills: ['code_review'],
    ...overrides,
  };
}

function jsonResponse(body: unknown, status = 200, statusText = 'OK'): Response {
  return new Response(JSON.stringify(body), {
    status,
    statusText,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('fetchRegistry', () => {
  beforeEach(() => {
    vi.unstubAllEnvs();
    vi.stubEnv('NODE_ENV', 'test');
    vi.stubEnv('REGISTRY_URL', REMOTE_REGISTRY_URL);
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  it('maps endpoints.http onto asap and drops unknown fields', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        jsonResponse({
          agents: [{ ...registryAgent(), owner_email: 'hidden@example.com' }],
        })
      )
    );

    const agents = await fetchRegistry();

    expect(agents).toHaveLength(1);
    expect(agents[0].endpoints).toEqual({
      asap: 'https://example.com/seed/0/asap',
      http: 'https://example.com/seed/0/asap',
    });
    expect(agents[0].version).toBe('2.0');
    expect(agents[0].capabilities.skills).toEqual([{ id: 'code_review', description: '' }]);
    expect(agents[0]).not.toHaveProperty('owner_email');
  });

  it('prefers endpoints.asap over endpoints.http and keeps asap_version', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        jsonResponse({
          agents: [
            registryAgent({
              asap_version: '1.0.0',
              endpoints: {
                asap: 'https://preferred.example/asap',
                http: 'https://legacy.example/asap',
              },
            }),
          ],
        })
      )
    );

    const agents = await fetchRegistry();

    expect(agents[0].endpoints).toEqual({
      asap: 'https://preferred.example/asap',
      http: 'https://legacy.example/asap',
    });
    expect(agents[0].version).toBe('1.0.0');
    expect(agents[0].capabilities.asap_version).toBe('1.0.0');
  });

  it('accepts a bare agent array', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse([registryAgent()])));

    const agents = await fetchRegistry();

    expect(agents).toHaveLength(1);
    expect(agents[0].id).toBe('urn:asap:agent:seed:agent-0');
    expect(agents[0].endpoints).toEqual({
      asap: 'https://example.com/seed/0/asap',
      http: 'https://example.com/seed/0/asap',
    });
  });

  it('returns an empty list on 404 without logging a fetch error', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(new Response(null, { status: 404, statusText: 'Not Found' }))
    );

    await expect(fetchRegistry()).resolves.toEqual([]);
    expect(console.error).not.toHaveBeenCalled();
  });

  it('returns an empty list and logs when the payload fails schema validation', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse({ agents: [{ id: 'urn:asap:agent:incomplete' }] }))
    );

    await expect(fetchRegistry()).resolves.toEqual([]);
    expect(console.error).toHaveBeenCalledTimes(1);
    expect(console.error).toHaveBeenCalledWith(
      'Registry schema validation failed:',
      expect.anything()
    );
  });

  it('returns an empty list when the registry responds with a server error', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(new Response(null, { status: 500, statusText: 'Server Error' }))
    );

    await expect(fetchRegistry()).resolves.toEqual([]);
    expect(console.error).toHaveBeenCalledTimes(1);
    expect(console.error).toHaveBeenCalledWith('Error fetching registry:', expect.any(Error));
  });
});

describe('fetchRevokedUrns', () => {
  beforeEach(() => {
    vi.unstubAllEnvs();
    vi.stubEnv('NODE_ENV', 'test');
    vi.stubEnv('REVOKED_URL', REMOTE_REVOKED_URL);
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  it('returns the urn set from a valid payload', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        revoked: [{ urn: 'urn:asap:agent:seed:agent-0' }, { urn: 'urn:asap:agent:seed:agent-1' }],
      })
    );
    vi.stubGlobal('fetch', fetchMock);

    await expect(fetchRevokedUrns()).resolves.toEqual(
      new Set(['urn:asap:agent:seed:agent-0', 'urn:asap:agent:seed:agent-1'])
    );
    expect(fetchMock).toHaveBeenCalledWith(REMOTE_REVOKED_URL, {
      next: { revalidate: REGISTRY_REVALIDATE_SECONDS },
    });
  });

  it('returns an empty set on 404 without logging', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(new Response(null, { status: 404, statusText: 'Not Found' }))
    );

    await expect(fetchRevokedUrns()).resolves.toEqual(new Set());
    expect(console.error).not.toHaveBeenCalled();
  });

  it('returns an empty set and logs once when the revoked list responds with 500', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(new Response(null, { status: 500, statusText: 'Server Error' }))
    );

    await expect(fetchRevokedUrns()).resolves.toEqual(new Set());
    expect(console.error).toHaveBeenCalledTimes(1);
    expect(console.error).toHaveBeenCalledWith('Failed to fetch revoked list: Server Error');
  });

  it('returns an empty set and logs when the revoked payload is not JSON', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(new Response('not-json', { status: 200, statusText: 'OK' }))
    );

    await expect(fetchRevokedUrns()).resolves.toEqual(new Set());
    expect(console.error).toHaveBeenCalledTimes(1);
    expect(console.error).toHaveBeenCalledWith(
      'Error fetching revoked list:',
      expect.any(SyntaxError)
    );
  });

  it('returns an empty set without logging when revoked is not an array', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse({ revoked: { urn: 'urn:asap:agent:x' } }))
    );

    await expect(fetchRevokedUrns()).resolves.toEqual(new Set());
    expect(console.error).not.toHaveBeenCalled();
  });
});
