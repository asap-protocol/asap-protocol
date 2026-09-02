import { describe, it, expect, vi, beforeEach } from 'vitest';
import { NextRequest } from 'next/server';
import { GET } from '../route';
import { isAllowedProxyUrlAsync } from '@/lib/url-validator-server';
import { checkProxyRateLimit } from '@/lib/rate-limit';

vi.mock('@/lib/url-validator-server');
vi.mock('@/lib/rate-limit', () => ({
  checkProxyRateLimit: vi.fn(),
}));

function createRequest(targetUrl: string, headers?: Record<string, string>): NextRequest {
  return new NextRequest(`http://localhost/api/proxy/check?url=${encodeURIComponent(targetUrl)}`, {
    method: 'GET',
    headers: headers ?? {},
  });
}

describe('GET /api/proxy/check', () => {
  beforeEach(() => {
    vi.mocked(isAllowedProxyUrlAsync).mockResolvedValue({ valid: true });
    vi.mocked(checkProxyRateLimit).mockResolvedValue({ allowed: true });
  });

  it('returns 400 when url parameter is missing', async () => {
    const req = new NextRequest('http://localhost/api/proxy/check', { method: 'GET' });
    const res = await GET(req);
    expect(res.status).toBe(400);
    const json = await res.json();
    expect(json.error).toBe('Invalid input');
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
    vi.mocked(checkProxyRateLimit).mockResolvedValue({ allowed: true });
    const req = createRequest('https://example.com/health', { 'x-real-ip': '203.0.113.5' });
    await GET(req);
    expect(checkProxyRateLimit).toHaveBeenCalledWith('203.0.113.5');
  });

  it('returns 400 for HTTP URL (HTTPS only)', async () => {
    vi.mocked(isAllowedProxyUrlAsync).mockResolvedValueOnce({
      valid: false,
      error: 'URL must use HTTPS only.',
    });
    const req = createRequest('http://example.com/health');
    const res = await GET(req);
    expect(res.status).toBe(400);
    const json = await res.json();
    expect(json.error).toContain('HTTPS');
  });

  it('returns 400 when URL resolves to private IP (DNS rebinding)', async () => {
    vi.mocked(isAllowedProxyUrlAsync).mockResolvedValueOnce({
      valid: false,
      error: 'Internal/Private network addresses are not allowed.',
    });
    const req = createRequest('https://attacker.example.com/health');
    const res = await GET(req);
    expect(res.status).toBe(400);
    const json = await res.json();
    expect(json.error).toContain('Private');
  });

  it('returns 400 for private IP hostname literal', async () => {
    vi.mocked(isAllowedProxyUrlAsync).mockResolvedValueOnce({
      valid: false,
      error: 'Internal/Private network addresses are not allowed.',
    });
    const req = createRequest('https://192.168.1.1/health');
    const res = await GET(req);
    expect(res.status).toBe(400);
    const json = await res.json();
    expect(json.error).toBeDefined();
  });

  it('returns 400 for localhost', async () => {
    vi.mocked(isAllowedProxyUrlAsync).mockResolvedValueOnce({
      valid: false,
      error: 'Internal/Private network addresses are not allowed.',
    });
    const req = createRequest('https://localhost/health');
    const res = await GET(req);
    expect(res.status).toBe(400);
  });

  it('returns ok:true when target returns 200', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200 }));
    const req = createRequest('https://example.com/health');
    const res = await GET(req);
    expect(res.status).toBe(200);
    const json = await res.json();
    expect(json.ok).toBe(true);
    expect(json.status).toBe(200);
  });

  it('returns ok:false when target returns 404', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 404 }));
    const req = createRequest('https://example.com/health');
    const res = await GET(req);
    expect(res.status).toBe(200);
    const json = await res.json();
    expect(json.ok).toBe(false);
    expect(json.status).toBe(404);
  });

  it('returns ok:false when fetch throws', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('ECONNREFUSED')));
    const req = createRequest('https://example.com/health');
    const res = await GET(req);
    expect(res.status).toBe(200);
    const json = await res.json();
    expect(json.ok).toBe(false);
    expect(json.status).toBe(0);
  });

  it('returns 400 when a 302 Location fails the allowlist', async () => {
    vi.mocked(isAllowedProxyUrlAsync).mockResolvedValueOnce({ valid: true }).mockResolvedValueOnce({
      valid: false,
      error: 'Internal/Private network addresses are not allowed.',
    });
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(null, {
          status: 302,
          headers: { Location: 'http://169.254.169.254/latest/meta-data/' },
        })
      )
    );
    const req = createRequest('https://example.com/health');
    const res = await GET(req);
    expect(res.status).toBe(400);
    const json = await res.json();
    expect(json.error).toContain('Private');
  });
});
