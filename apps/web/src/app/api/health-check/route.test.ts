import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { NextRequest } from 'next/server';
import { GET } from './route';
import { isAllowedExternalUrl } from '@/lib/url-validator';
import { checkProxyRateLimit } from '@/lib/rate-limit';

vi.mock('@/lib/url-validator', () => ({
  isAllowedExternalUrl: vi.fn(),
}));

vi.mock('@/lib/rate-limit', () => ({
  checkProxyRateLimit: vi.fn(),
}));

function createRequest(url?: string, headers?: Record<string, string>): NextRequest {
  const base = 'http://localhost/api/health-check';
  const target = url ? `${base}?url=${encodeURIComponent(url)}` : base;
  return new NextRequest(target, { method: 'GET', headers: headers ?? {} });
}

describe('GET /api/health-check', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(checkProxyRateLimit).mockResolvedValue({ allowed: true });
    vi.mocked(isAllowedExternalUrl).mockResolvedValue({ valid: true });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('returns 400 when url parameter is missing', async () => {
    const res = await GET(createRequest());
    expect(res.status).toBe(400);
    const json = await res.json();
    expect(json.error).toBe('Invalid input');
  });

  it('returns 429 and Retry-After when rate limit blocks request', async () => {
    vi.mocked(checkProxyRateLimit).mockResolvedValue({ allowed: false, retryAfter: 12 });
    const res = await GET(
      createRequest('https://example.com/health', {
        'x-forwarded-for': '203.0.113.10, 198.51.100.1',
      })
    );
    expect(res.status).toBe(429);
    expect(res.headers.get('Retry-After')).toBe('12');
    const json = await res.json();
    expect(json.error).toBe('Too many requests');
    expect(json.retryAfter).toBe(12);
    expect(checkProxyRateLimit).toHaveBeenCalledWith('203.0.113.10');
  });

  it('returns 400 when URL fails allowlist validation', async () => {
    vi.mocked(isAllowedExternalUrl).mockResolvedValue({
      valid: false,
      error: 'Internal/Private network addresses are not allowed.',
    });
    const res = await GET(createRequest('https://attacker.example.com/health'));
    expect(res.status).toBe(400);
    const json = await res.json();
    expect(json.error).toContain('Internal/Private');
  });

  it('returns ok and status when fetch succeeds', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 204 }));
    const res = await GET(createRequest('https://example.com/health'));
    expect(res.status).toBe(200);
    const json = await res.json();
    expect(json).toEqual({ ok: true, status: 204 });
  });

  it('returns fallback payload when fetch throws', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('timeout')));
    const res = await GET(createRequest('https://example.com/health'));
    expect(res.status).toBe(200);
    const json = await res.json();
    expect(json).toEqual({ ok: false, status: 0 });
  });

  it('returns 400 when a 302 Location fails the allowlist', async () => {
    vi.mocked(isAllowedExternalUrl).mockResolvedValueOnce({ valid: true }).mockResolvedValueOnce({
      valid: false,
      error: 'Internal/Private network addresses are not allowed.',
    });
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(null, {
          status: 302,
          headers: { Location: 'http://127.0.0.1:8080/secret' },
        })
      )
    );
    const res = await GET(createRequest('https://example.com/health'));
    expect(res.status).toBe(400);
    const json = await res.json();
    expect(json.error).toContain('Internal/Private');
  });
});
