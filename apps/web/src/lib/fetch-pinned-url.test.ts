import { describe, expect, it, vi, afterEach } from 'vitest';
import http from 'node:http';
import type { AddressInfo } from 'node:net';
import {
  fetchAllowlistedUrl,
  isPinnedFetchBlocked,
  pickPinnedIp,
  pinnedLookup,
} from './fetch-pinned-url';

describe('pickPinnedIp', () => {
  it('prefers IPv4 when both families are present', () => {
    expect(pickPinnedIp(['2001:db8::1', '93.184.216.34'])).toBe('93.184.216.34');
  });

  it('returns IPv6 when that is all that is available', () => {
    expect(pickPinnedIp(['2001:db8::1'])).toBe('2001:db8::1');
  });
});

describe('pinnedLookup', () => {
  it('ignores hostname and returns the pinned address (single form)', () => {
    const lookup = pinnedLookup('203.0.113.10');
    const result = new Promise<{ address: string; family: number }>((resolve, reject) => {
      lookup('evil.example', { family: 0 }, (err, address, family) => {
        if (err) {
          reject(err);
          return;
        }
        resolve({ address: String(address), family: family ?? 0 });
      });
    });
    return expect(result).resolves.toEqual({ address: '203.0.113.10', family: 4 });
  });

  it('returns address list when Node requests all:true', () => {
    const lookup = pinnedLookup('203.0.113.10');
    const result = new Promise<unknown>((resolve, reject) => {
      lookup('evil.example', { all: true, family: 0 }, (err, addresses) => {
        if (err) {
          reject(err);
          return;
        }
        resolve(addresses);
      });
    });
    return expect(result).resolves.toEqual([{ address: '203.0.113.10', family: 4 }]);
  });
});

describe('fetchAllowlistedUrl', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('connects to the pinned IP even if DNS would rebind the hostname', async () => {
    const server = http.createServer((req, res) => {
      res.writeHead(200, { 'content-type': 'text/plain' });
      res.end('ok');
    });
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
    const { port } = server.address() as AddressInfo;

    try {
      const result = await fetchAllowlistedUrl(
        `http://rebind.example.invalid:${port}/`,
        async () => ({ valid: true, ips: ['127.0.0.1'] }),
        2000
      );
      expect(isPinnedFetchBlocked(result)).toBe(false);
      if (!isPinnedFetchBlocked(result)) {
        expect(result).toEqual({ ok: true, status: 200 });
      }
    } finally {
      await new Promise<void>((resolve, reject) =>
        server.close((err) => (err ? reject(err) : resolve()))
      );
    }
  });

  it('re-validates redirect targets and blocks private Location hops', async () => {
    const server = http.createServer((req, res) => {
      if (req.url === '/start') {
        res.writeHead(302, { Location: 'http://127.0.0.1/secret' });
        res.end();
        return;
      }
      res.writeHead(200);
      res.end('should-not-reach');
    });
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
    const { port } = server.address() as AddressInfo;

    try {
      const result = await fetchAllowlistedUrl(
        `http://rebind.example.invalid:${port}/start`,
        async (url) => {
          if (url.includes('/start')) {
            return { valid: true, ips: ['127.0.0.1'] };
          }
          return { valid: false, error: 'URL not allowed: private redirect' };
        },
        2000
      );
      expect(isPinnedFetchBlocked(result)).toBe(true);
      if (isPinnedFetchBlocked(result)) {
        expect(result.error).toContain('not allowed');
      }
    } finally {
      await new Promise<void>((resolve, reject) =>
        server.close((err) => (err ? reject(err) : resolve()))
      );
    }
  });
});
