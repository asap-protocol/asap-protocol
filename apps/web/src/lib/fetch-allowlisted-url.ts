import type { AllowedUrlResult } from '@/lib/url-validator';

/** Node/undici `fetch` follows 3xx by default, which would skip the SSRF allowlist. */
export const ALLOWLISTED_FETCH_REDIRECT = 'manual' as const;

const MAX_REDIRECTS = 5;
const REDIRECT_STATUSES = new Set([301, 302, 303, 307, 308]);

export type AllowlistedFetchOk = {
  ok: boolean;
  status: number;
};

export type AllowlistedFetchBlocked = {
  error: string;
};

export type AllowlistedFetchResult = AllowlistedFetchOk | AllowlistedFetchBlocked;

export function isAllowlistedFetchBlocked(
  result: AllowlistedFetchResult
): result is AllowlistedFetchBlocked {
  return 'error' in result;
}

/**
 * GET a URL that already passed the caller allowlist, without following
 * redirects to hosts the allowlist would reject (loopback, RFC1918, metadata).
 *
 * Example:
 *   const result = await fetchAllowlistedUrl(url, isAllowedExternalUrl, 3000);
 *   if (isAllowlistedFetchBlocked(result)) return 400;
 *
 * @param url - Already-allowlisted starting URL
 * @param validate - Same allowlist used for the initial URL; applied to each Location
 * @param timeoutMs - Abort each hop after this many milliseconds
 */
export async function fetchAllowlistedUrl(
  url: string,
  validate: (nextUrl: string) => Promise<AllowedUrlResult>,
  timeoutMs: number
): Promise<AllowlistedFetchResult> {
  let currentUrl = url;
  for (let hop = 0; hop <= MAX_REDIRECTS; hop++) {
    let response: Response;
    try {
      response = await fetchOneAllowlistedHop(currentUrl, timeoutMs);
    } catch {
      return { ok: false, status: 0 };
    }
    if (!REDIRECT_STATUSES.has(response.status)) {
      return { ok: response.ok, status: response.status };
    }
    const nextUrl = nextRedirectUrl(response, currentUrl);
    if (nextUrl === null) {
      return { ok: false, status: response.status };
    }
    const check = await validate(nextUrl);
    if (!check.valid) {
      return {
        error:
          check.error ?? `Redirect target not allowed: ${nextUrl} (expected a public HTTP(S) URL)`,
      };
    }
    currentUrl = nextUrl;
  }
  return { error: `Too many redirects (max ${MAX_REDIRECTS}) from ${url}` };
}

async function fetchOneAllowlistedHop(url: string, timeoutMs: number): Promise<Response> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, {
      method: 'GET',
      redirect: ALLOWLISTED_FETCH_REDIRECT,
      credentials: 'omit',
      signal: controller.signal,
    });
  } finally {
    clearTimeout(timeoutId);
  }
}

function nextRedirectUrl(response: Response, currentUrl: string): string | null {
  const location = response.headers.get('location');
  if (!location) {
    return null;
  }
  try {
    return new URL(location, currentUrl).href;
  } catch {
    return null;
  }
}
