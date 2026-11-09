/** CORS allowlist and preflight fences for the proxy middleware. */
import { describe, it, expect, vi } from 'vitest';
import { NextRequest } from 'next/server';

vi.mock('@/auth', () => ({
  auth: (fn: (req: NextRequest & { auth?: unknown }) => Response) => fn,
}));

const baseUrl = 'http://localhost:3000';
const middlewareContext = { params: Promise.resolve({}) };

function createRequest(
  pathname: string,
  options: { origin?: string; method?: string } = {}
): NextRequest {
  const url = new URL(pathname, baseUrl);
  const headers = new Headers();
  if (options.origin) headers.set('origin', options.origin);
  return new NextRequest(url.toString(), {
    method: options.method ?? 'GET',
    headers,
  });
}

describe('proxy CORS allowlist', () => {
  it('allows 127.0.0.1 with another port only in development', async () => {
    vi.stubEnv('NODE_ENV', 'development');
    vi.stubEnv('NEXT_PUBLIC_APP_URL', 'http://localhost:3000');
    try {
      const { default: proxy } = await import('@/proxy');
      const req = createRequest('/api/health-check', { origin: 'http://127.0.0.1:3001' });
      const res = (await proxy(req, middlewareContext)) as Response;
      expect(res.status).toBe(200);
      expect(res.headers.get('Access-Control-Allow-Origin')).toBe('http://127.0.0.1:3001');
    } finally {
      vi.unstubAllEnvs();
    }
  });

  it('rejects another loopback port when NODE_ENV is test', async () => {
    vi.stubEnv('NODE_ENV', 'test');
    vi.stubEnv('NEXT_PUBLIC_APP_URL', 'http://localhost:3000');
    try {
      const { default: proxy } = await import('@/proxy');
      const localhostOtherPort = createRequest('/api/health-check', {
        origin: 'http://localhost:3001',
      });
      const deniedLocalhost = (await proxy(localhostOtherPort, middlewareContext)) as Response;
      expect(deniedLocalhost.status).toBe(403);

      const loopbackOtherPort = createRequest('/api/health-check', {
        origin: 'http://127.0.0.1:3001',
      });
      const deniedLoopback = (await proxy(loopbackOtherPort, middlewareContext)) as Response;
      expect(deniedLoopback.status).toBe(403);

      const configured = createRequest('/api/health-check', {
        origin: 'http://localhost:3000',
      });
      const allowed = (await proxy(configured, middlewareContext)) as Response;
      expect(allowed.status).toBe(200);
      expect(allowed.headers.get('Access-Control-Allow-Origin')).toBe('http://localhost:3000');
    } finally {
      vi.unstubAllEnvs();
    }
  });

  it('rejects a malformed Origin in development', async () => {
    vi.stubEnv('NODE_ENV', 'development');
    try {
      const { default: proxy } = await import('@/proxy');
      const req = createRequest('/api/health-check', { origin: 'http://[' });
      const res = (await proxy(req, middlewareContext)) as Response;
      expect(res.status).toBe(403);
      const json = await res.json();
      expect(json.error).toBe('Forbidden');
    } finally {
      vi.unstubAllEnvs();
    }
  });

  it('answers an allowed OPTIONS preflight with 204', async () => {
    const { default: proxy } = await import('@/proxy');
    const req = createRequest('/api/health-check', {
      origin: 'http://localhost:3000',
      method: 'OPTIONS',
    });
    const res = (await proxy(req, middlewareContext)) as Response;
    expect(res.status).toBe(204);
    expect(await res.text()).toBe('');
    expect(res.headers.get('Access-Control-Allow-Origin')).toBe('http://localhost:3000');
    expect(res.headers.get('Access-Control-Allow-Methods')).toBe('GET, POST, PUT, DELETE, OPTIONS');
    expect(res.headers.get('Access-Control-Allow-Headers')).toBe(
      'Content-Type, Authorization, X-Requested-With'
    );
    expect(res.headers.get('Access-Control-Max-Age')).toBe('86400');
  });

  it('rejects OPTIONS when Origin is not on the allowlist', async () => {
    const { default: proxy } = await import('@/proxy');
    const req = createRequest('/api/health-check', {
      origin: 'https://evil.com',
      method: 'OPTIONS',
    });
    const res = (await proxy(req, middlewareContext)) as Response;
    expect(res.status).toBe(403);
    const json = await res.json();
    expect(json.error).toBe('Forbidden');
  });
});
