import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  ALLOWLISTED_FETCH_REDIRECT,
  fetchAllowlistedUrl,
  isAllowlistedFetchBlocked,
} from '@/lib/fetch-allowlisted-url';
import type { AllowedUrlResult } from '@/lib/url-validator';

function allowPublicHttp(url: string): Promise<AllowedUrlResult> {
  const parsed = new URL(url);
  const host = parsed.hostname.toLowerCase();
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    return Promise.resolve({ valid: false, error: `unsupported scheme: ${url}` });
  }
  if (host === 'localhost' || host.startsWith('127.') || host === '169.254.169.254') {
    return Promise.resolve({
      valid: false,
      error: `Internal/Private network addresses are not allowed: ${host}`,
    });
  }
  return Promise.resolve({ valid: true });
}

describe('fetchAllowlistedUrl', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('fetches with redirect=manual so undici cannot skip the allowlist', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal('fetch', fetchMock);

    const result = await fetchAllowlistedUrl('https://example.com/health', allowPublicHttp, 3000);

    expect(result).toEqual({ ok: true, status: 204 });
    expect(fetchMock).toHaveBeenCalledWith(
      'https://example.com/health',
      expect.objectContaining({
        method: 'GET',
        redirect: ALLOWLISTED_FETCH_REDIRECT,
        credentials: 'omit',
      })
    );
  });

  it('follows a redirect only after the Location host passes the allowlist', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(null, {
          status: 302,
          headers: { Location: 'https://cdn.example.com/health' },
        })
      )
      .mockResolvedValueOnce(new Response(null, { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    const result = await fetchAllowlistedUrl('https://example.com/health', allowPublicHttp, 3000);

    expect(result).toEqual({ ok: true, status: 200 });
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      'https://cdn.example.com/health',
      expect.objectContaining({ redirect: ALLOWLISTED_FETCH_REDIRECT })
    );
  });

  it('does not fetch a redirect Location that points at loopback', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(null, {
        status: 302,
        headers: { Location: 'http://127.0.0.1:8080/secret' },
      })
    );
    vi.stubGlobal('fetch', fetchMock);

    const result = await fetchAllowlistedUrl(
      'https://attacker.example/ssrf',
      allowPublicHttp,
      3000
    );

    expect(isAllowlistedFetchBlocked(result)).toBe(true);
    if (isAllowlistedFetchBlocked(result)) {
      expect(result.error).toContain('127.0.0.1');
    }
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).not.toHaveBeenCalledWith(
      expect.stringContaining('127.0.0.1'),
      expect.anything()
    );
  });

  it('does not fetch a redirect Location that points at link-local metadata', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(null, {
        status: 301,
        headers: { Location: 'http://169.254.169.254/latest/meta-data/' },
      })
    );
    vi.stubGlobal('fetch', fetchMock);

    const result = await fetchAllowlistedUrl(
      'https://attacker.example/metadata',
      allowPublicHttp,
      3000
    );

    expect(isAllowlistedFetchBlocked(result)).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('resolves relative Location against the current URL before validating', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(null, {
          status: 302,
          headers: { Location: '/health' },
        })
      )
      .mockResolvedValueOnce(new Response(null, { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    const result = await fetchAllowlistedUrl('https://example.com/status', allowPublicHttp, 3000);

    expect(result).toEqual({ ok: true, status: 200 });
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      'https://example.com/health',
      expect.objectContaining({ redirect: ALLOWLISTED_FETCH_REDIRECT })
    );
  });

  it('returns ok:false when fetch throws', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('timeout')));
    const result = await fetchAllowlistedUrl('https://example.com/health', allowPublicHttp, 3000);
    expect(result).toEqual({ ok: false, status: 0 });
  });
});
