import http from 'node:http';
import https from 'node:https';
import type { LookupFunction } from 'node:net';

import type { AllowedUrlResult } from '@/lib/url-validator';

/** Node `http`/`https` do not follow redirects; we walk Location so each hop is allowlisted. */
const MAX_REDIRECTS = 5;
const REDIRECT_STATUSES = new Set([301, 302, 303, 307, 308]);

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

/**
 * GET `url` while connecting only to `pinnedIp` (Host/SNI still use the URL hostname).
 */
export function httpGetPinned(
    url: string,
    pinnedIp: string,
    timeoutMs: number
): Promise<PinnedHopResult> {
    const parsed = new URL(url);
    const transport = parsed.protocol === 'https:' ? https : http;
    const lookup = pinnedLookup(pinnedIp);

    return new Promise((resolve, reject) => {
        const req = transport.request(
            url,
            {
                method: 'GET',
                lookup,
                // Keep SNI/cert validation on the original hostname while dialing the pinned IP.
                servername: parsed.hostname.replace(/^\[|\]$/g, ''),
                timeout: timeoutMs,
            },
            (res) => {
                res.resume();
                const status = res.statusCode ?? 0;
                const rawLocation = res.headers.location;
                const location = Array.isArray(rawLocation) ? rawLocation[0] : rawLocation;
                resolve({
                    ok: status >= 200 && status < 300,
                    status,
                    location,
                });
            }
        );
        req.on('timeout', () => {
            req.destroy(new Error('Request timed out'));
        });
        req.on('error', reject);
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

export type ResolveAllowlisted = (
    url: string
) => Promise<AllowedUrlResult & { ips?: string[] }>;

/**
 * Allowlisted GET that pins connect() to a DNS result from `validate`, and
 * re-validates + re-pins each redirect hop (also blocks open-redirect SSRF).
 */
export async function fetchAllowlistedUrl(
    url: string,
    validate: ResolveAllowlisted,
    timeoutMs: number
): Promise<PinnedFetchResult> {
    let currentUrl = url;
    for (let hop = 0; hop <= MAX_REDIRECTS; hop++) {
        const check = await validate(currentUrl);
        if (!check.valid) {
            return {
                error: check.error ?? `URL not allowed: ${currentUrl}`,
            };
        }
        const ips = check.ips;
        if (!ips || ips.length === 0) {
            return {
                error: `Allowlist did not return pinned IPs for ${currentUrl}`,
            };
        }

        let hopResult: PinnedHopResult;
        try {
            hopResult = await httpGetPinned(currentUrl, pickPinnedIp(ips), timeoutMs);
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
