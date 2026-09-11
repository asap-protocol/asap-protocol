import http from 'node:http';
import https from 'node:https';
import type { LookupFunction } from 'node:net';

import type { AllowedUrlResult } from '@/lib/url-validator';

/** Node `http`/`https` do not follow redirects; we walk Location so each hop is allowlisted. */
const MAX_REDIRECTS = 5;
const REDIRECT_STATUSES = new Set([301, 302, 303, 307, 308]);

export type PinnedHttpMethod = 'GET' | 'HEAD';

export type PinnedFetchOk = {
  ok: boolean;
  status: number;
};

export type PinnedFetchBlocked = {
  error: string;
};

export type PinnedFetchResult = PinnedFetchOk | PinnedFetchBlocked;

export function isPinnedFetchBlocked(result: PinnedFetchResult): result is PinnedFetchBlocked {
  return 'error' in result;
}

/**
 * Build a `net.LookupFunction` that always returns a pre-validated public IP.
 *
 * The SSRF allowlist resolves DNS once; Node `fetch` would resolve again and allow
 * DNS rebinding (public A/AAAA for the allowlist check, then loopback/IMDS on connect).
 * Pinning the validated address closes that TOCTOU.
 */
export function pinnedLookup(ip: string): LookupFunction {
  const family: 4 | 6 = ip.includes(':') ? 6 : 4;
  return (hostname, options, callback) => {
    void hostname;
    // Node's http(s) agent commonly calls lookup with `{ all: true }` and expects
    // `callback(err, [{ address, family }, ...])` — the single-address form breaks.
    if (options.all) {
      callback(null, [{ address: ip, family }]);
      return;
    }
    callback(null, ip, family);
  };
}

/** Prefer IPv4 when the allowlist returned both families (IMDS and most probes are v4). */
export function pickPinnedIp(ips: string[]): string {
  if (ips.length === 0) {
    throw new Error('pickPinnedIp requires at least one IP');
  }
  return ips.find((candidate) => !candidate.includes(':')) ?? ips[0]!;
}

type PinnedHopResult = PinnedFetchOk & { location?: string };

function settlePinnedHop(
  req: http.ClientRequest,
  res: http.IncomingMessage,
  resolveOnce: (value: PinnedHopResult) => void
): void {
  const status = res.statusCode ?? 0;
  const rawLocation = res.headers.location;
  const location = Array.isArray(rawLocation) ? rawLocation[0] : rawLocation;
  resolveOnce({
    ok: status >= 200 && status < 300,
    status,
    location,
  });
  // Headers are enough for reachability/redirect policy; drop the body immediately.
  req.destroy();
}

type Settler<T> = {
  resolveOnce: (value: T) => void;
  rejectOnce: (err: Error) => void;
};

function createSettler<T>(resolve: (value: T) => void, reject: (err: Error) => void): Settler<T> {
  let settled = false;
  return {
    resolveOnce(value: T): void {
      if (settled) return;
      settled = true;
      resolve(value);
    },
    rejectOnce(err: Error): void {
      if (settled) return;
      settled = true;
      reject(err);
    },
  };
}

/**
 * GET/HEAD `url` while connecting only to `pinnedIp` (Host/SNI still use the URL hostname).
 *
 * Example: `await httpGetPinned(url, '203.0.113.10', 3000)` dials that IP only.
 */
export function httpGetPinned(
  url: string,
  pinnedIp: string,
  timeoutMs: number,
  method: PinnedHttpMethod = 'GET'
): Promise<PinnedHopResult> {
  const parsed = new URL(url);
  const transport = parsed.protocol === 'https:' ? https : http;
  return new Promise((resolve, reject) => {
    const { resolveOnce, rejectOnce } = createSettler(resolve, reject);
    const req = transport.request(
      url,
      {
        method,
        lookup: pinnedLookup(pinnedIp),
        // One-shot agent: do not return an attacker socket to the global keep-alive pool.
        agent: false,
        // Keep SNI/cert validation on the original hostname while dialing the pinned IP.
        servername: parsed.hostname.replace(/^\[|\]$/g, ''),
        timeout: timeoutMs,
      },
      (res) => {
        settlePinnedHop(req, res, resolveOnce);
      }
    );
    req.on('timeout', () => {
      req.destroy(new Error('Request timed out'));
    });
    req.on('error', rejectOnce);
    req.end();
  });
}

function redirectTarget(responseUrl: string, location: string | undefined): string | null {
  if (!location) {
    return null;
  }
  try {
    return new URL(location, responseUrl).href;
  } catch {
    return null;
  }
}

export type ResolveAllowlisted = (url: string) => Promise<AllowedUrlResult & { ips?: string[] }>;

async function pinnedIpsForUrl(
  currentUrl: string,
  validate: ResolveAllowlisted
): Promise<{ ips: string[] } | PinnedFetchBlocked> {
  const check = await validate(currentUrl);
  if (!check.valid) {
    return { error: check.error ?? `URL not allowed: ${currentUrl}` };
  }
  if (!check.ips || check.ips.length === 0) {
    return { error: `Allowlist did not return pinned IPs for ${currentUrl}` };
  }
  return { ips: check.ips };
}

/**
 * Allowlisted GET/HEAD that pins connect() to a DNS result from `validate`, and
 * re-validates + re-pins each redirect hop (also blocks open-redirect SSRF).
 * `timeoutMs` is a shared wall-clock budget for the whole walk, not per hop.
 *
 * Example: `await fetchAllowlistedUrl(url, isAllowedExternalUrl, 3000)`
 */
export async function fetchAllowlistedUrl(
  url: string,
  validate: ResolveAllowlisted,
  timeoutMs: number,
  method: PinnedHttpMethod = 'GET'
): Promise<PinnedFetchResult> {
  let currentUrl = url;
  const deadlineMs = Date.now() + timeoutMs;
  for (let hop = 0; hop <= MAX_REDIRECTS; hop++) {
    const remainingMs = deadlineMs - Date.now();
    if (remainingMs <= 0) {
      return { ok: false, status: 0 };
    }
    const pinned = await pinnedIpsForUrl(currentUrl, validate);
    if ('error' in pinned) {
      return pinned;
    }
    const hopBudgetMs = deadlineMs - Date.now();
    if (hopBudgetMs <= 0) {
      return { ok: false, status: 0 };
    }

    let hopResult: PinnedHopResult;
    try {
      hopResult = await httpGetPinned(currentUrl, pickPinnedIp(pinned.ips), hopBudgetMs, method);
    } catch {
      return { ok: false, status: 0 };
    }

    if (!REDIRECT_STATUSES.has(hopResult.status)) {
      return { ok: hopResult.ok, status: hopResult.status };
    }

    const next = redirectTarget(currentUrl, hopResult.location);
    if (next === null) {
      return { ok: false, status: hopResult.status };
    }
    currentUrl = next;
  }
  return { error: `Too many redirects (max ${MAX_REDIRECTS}) from ${url}` };
}
