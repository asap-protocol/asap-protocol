import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { NextRequest } from 'next/server';
import { GET } from './route';
import { isAllowedExternalUrl } from '@/lib/url-validator';
import { checkProxyRateLimit } from '@/lib/rate-limit';
import { fetchAllowlistedUrl } from '@/lib/fetch-pinned-url';

vi.mock('@/lib/url-validator', () => ({
  isAllowedExternalUrl: vi.fn(),
}));

vi.mock('@/lib/rate-limit', () => ({
  checkProxyRateLimit: vi.fn(),
}));

vi.mock('@/lib/fetch-pinned-url', async () => {
  const actual = await vi.importActual<typeof import('@/lib/fetch-pinned-url')>('@/lib/fetch-pinned-url');
  return {
    ...actual,
    fetchAllowlistedUrl: vi.fn(),
  };
});

function createRequest(url?: string, headers?: Record<string, string>): NextRequest {
  const base = 'http://localhost/api/health-check';
  const target = url ? `${base}?url=${encodeURIComponent(url)}` : base;
  return new NextRequest(target, { method: 'GET', headers: headers ?? {} });
}

describe('GET /api/health-check', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(checkProxyRateLimit).mockResolvedValue({ allowed: true });
    vi.mocked(isAllowedExternalUrl).mockResolvedValue({ valid: true, ips: ['93.184.216.34'] });
    vi.mocked(fetchAllowlistedUrl).mockResolvedValue({ ok: true, status: 204 });
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
      createRequest('https://example.com/health', { 'x-forwarded-for': '203.0.113.10, 198.51.100.1' })
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

  it('returns ok and status when pinned fetch succeeds', async () => {
    vi.mocked(fetchAllowlistedUrl).mockResolvedValue({ ok: true, status: 204 });
    const res = await GET(createRequest('https://example.com/health'));
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ok: true, status: 204 });
    expect(fetchAllowlistedUrl).toHaveBeenCalled();
  });

  it('returns fallback payload when pinned fetch reports status 0', async () => {
    vi.mocked(fetchAllowlistedUrl).mockResolvedValue({ ok: false, status: 0 });
    const res = await GET(createRequest('https://example.com/health'));
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ok: false, status: 0 });
  });

  it('returns 400 when redirect hop fails allowlist during pinned fetch', async () => {
    vi.mocked(fetchAllowlistedUrl).mockResolvedValue({
      error: 'URL not allowed: http://127.0.0.1/',
    });
    const res = await GET(createRequest('https://example.com/health'));
    expect(res.status).toBe(400);
    expect((await res.json()).error).toContain('not allowed');
  });
});
