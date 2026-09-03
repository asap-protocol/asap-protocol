import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { NextRequest } from 'next/server';
import { GET } from '../route';
import { isAllowedProxyUrlAsync } from '@/lib/url-validator-server';
import { checkProxyRateLimit } from '@/lib/rate-limit';
import { fetchAllowlistedUrl } from '@/lib/fetch-pinned-url';

vi.mock('@/lib/url-validator-server');
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

function createRequest(targetUrl: string, headers?: Record<string, string>): NextRequest {
  return new NextRequest(
    `http://localhost/api/proxy/check?url=${encodeURIComponent(targetUrl)}`,
    { method: 'GET', headers: headers ?? {} }
  );
}

describe('GET /api/proxy/check', () => {
  beforeEach(() => {
    vi.mocked(isAllowedProxyUrlAsync).mockResolvedValue({ valid: true, ips: ['93.184.216.34'] });
    vi.mocked(checkProxyRateLimit).mockResolvedValue({ allowed: true });
    vi.mocked(fetchAllowlistedUrl).mockResolvedValue({ ok: true, status: 200 });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('returns 400 when url parameter is missing', async () => {
    const req = new NextRequest('http://localhost/api/proxy/check', { method: 'GET' });
    const res = await GET(req);
    expect(res.status).toBe(400);
    expect((await res.json()).error).toBe('Invalid input');
  });

  it('returns 429 and Retry-After when rate limit blocks request', async () => {
    vi.mocked(checkProxyRateLimit).mockResolvedValue({ allowed: false, retryAfter: 9 });
    const req = createRequest('https://example.com/health', { 'x-forwarded-for': '198.51.100.20' });
    const res = await GET(req);
    expect(res.status).toBe(429);
    expect(res.headers.get('Retry-After')).toBe('9');
    expect(await res.json()).toEqual({ error: 'Too many requests', retryAfter: 9 });
    expect(checkProxyRateLimit).toHaveBeenCalledWith('198.51.100.20');
  });

  it('uses x-real-ip when x-forwarded-for is absent', async () => {
    const req = createRequest('https://example.com/health', { 'x-real-ip': '203.0.113.5' });
    await GET(req);
    expect(checkProxyRateLimit).toHaveBeenCalledWith('203.0.113.5');
  });

  it('returns 400 for HTTP URL (HTTPS only)', async () => {
    vi.mocked(isAllowedProxyUrlAsync).mockResolvedValueOnce({
      valid: false,
      error: 'URL must use HTTPS only.',
    });
    const res = await GET(createRequest('http://example.com/health'));
    expect(res.status).toBe(400);
    expect((await res.json()).error).toContain('HTTPS');
  });

  it('returns 400 when URL resolves to private IP (DNS rebinding allowlist)', async () => {
    vi.mocked(isAllowedProxyUrlAsync).mockResolvedValueOnce({
      valid: false,
      error: 'Internal/Private network addresses are not allowed.',
    });
    const res = await GET(createRequest('https://attacker.example.com/health'));
    expect(res.status).toBe(400);
    expect((await res.json()).error).toContain('Private');
  });

  it('returns ok:true when pinned fetch succeeds', async () => {
    vi.mocked(fetchAllowlistedUrl).mockResolvedValue({ ok: true, status: 200 });
    const res = await GET(createRequest('https://example.com/health'));
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ok: true, status: 200 });
    expect(fetchAllowlistedUrl).toHaveBeenCalled();
  });

  it('returns ok:false when pinned fetch reports 404', async () => {
    vi.mocked(fetchAllowlistedUrl).mockResolvedValue({ ok: false, status: 404 });
    const res = await GET(createRequest('https://example.com/health'));
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ok: false, status: 404 });
  });

  it('returns ok:false when pinned fetch reports status 0', async () => {
    vi.mocked(fetchAllowlistedUrl).mockResolvedValue({ ok: false, status: 0 });
    const res = await GET(createRequest('https://example.com/health'));
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ok: false, status: 0 });
  });

  it('returns 400 when redirect hop fails allowlist during pinned fetch', async () => {
    vi.mocked(fetchAllowlistedUrl).mockResolvedValue({
      error: 'URL not allowed: http://169.254.169.254/',
    });
    const res = await GET(createRequest('https://example.com/health'));
    expect(res.status).toBe(400);
    expect((await res.json()).error).toContain('not allowed');
  });
});
