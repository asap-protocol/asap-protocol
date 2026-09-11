import { promises as dns } from 'node:dns';
import { isBlockedHostOrIp } from './url-validator-ip';

/** SSRF validation: blocks private IPs, loopback, cloud metadata. */

export interface AllowedUrlResult {
    valid: boolean;
    error?: string;
    /** Public IPs from the allowlist DNS lookup; pin connect() to these against DNS rebinding. */
    ips?: string[];
}

export async function isAllowedExternalUrl(url: string): Promise<AllowedUrlResult> {
    try {
        const parsed = new URL(url);
        if (parsed.protocol !== 'https:' && parsed.protocol !== 'http:') {
            return { valid: false, error: 'URL must use HTTP or HTTPS.' };
        }
        const hostname = parsed.hostname.toLowerCase();
        if (isBlockedHostOrIp(hostname)) {
            return { valid: false, error: 'Internal/Private network addresses are not allowed.' };
        }

        const bareHostname = hostname.replace(/^\[|\]$/g, '');
        const [v4, v6] = await Promise.allSettled([
            dns.resolve4(bareHostname),
            dns.resolve6(bareHostname),
        ]);

        const ips: string[] = [];
        if (v4.status === 'fulfilled' && Array.isArray(v4.value)) ips.push(...v4.value);
        if (v6.status === 'fulfilled' && Array.isArray(v6.value)) ips.push(...v6.value);
        if (ips.length === 0) {
            return { valid: false, error: 'Could not resolve hostname.' };
        }

        for (const ip of ips) {
            if (isBlockedHostOrIp(ip)) {
                return {
                    valid: false,
                    error: `Resolved IP (${ip}) is an internal/private address.`,
                };
            }
        }

        return { valid: true, ips };
    } catch {
        return { valid: false, error: 'Invalid URL.' };
    }
}

/** Sync check: hostname literal only (no DNS). */
export function isAllowedProxyUrl(url: string): AllowedUrlResult {
    try {
        const parsed = new URL(url);
        if (parsed.protocol !== 'https:') {
            return { valid: false, error: 'URL must use HTTPS only.' };
        }
        const hostname = parsed.hostname.toLowerCase();
        if (isBlockedHostOrIp(hostname)) {
            return { valid: false, error: 'Internal/Private network addresses are not allowed.' };
        }
        return { valid: true };
    } catch {
        return { valid: false, error: 'Invalid URL.' };
    }
}
