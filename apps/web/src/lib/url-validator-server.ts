import { promises as dns } from 'node:dns';
import { isAllowedProxyUrl, AllowedUrlResult } from './url-validator';
import { isBlockedHostOrIp } from './url-validator-ip';

export type { AllowedUrlResult } from './url-validator';

export async function isAllowedProxyUrlAsync(url: string): Promise<AllowedUrlResult> {
    const sync = isAllowedProxyUrl(url);
    if (!sync.valid) return sync;

    try {
        const parsed = new URL(url);
        const hostname = parsed.hostname.replace(/^\[|\]$/g, '');

        const [v4, v6] = await Promise.allSettled([
            dns.resolve4(hostname),
            dns.resolve6(hostname),
        ]);

        const ips: string[] = [];
        if (v4.status === 'fulfilled' && Array.isArray(v4.value)) ips.push(...v4.value);
        if (v6.status === 'fulfilled' && Array.isArray(v6.value)) ips.push(...v6.value);
        if (ips.length === 0) {
            return { valid: false, error: 'Could not resolve hostname.' };
        }

        for (const ip of ips) {
            if (isBlockedHostOrIp(ip)) {
                return { valid: false, error: 'Internal/Private network addresses are not allowed.' };
            }
        }
        return { valid: true, ips };
    } catch {
        return { valid: false, error: 'Invalid URL.' };
    }
}
