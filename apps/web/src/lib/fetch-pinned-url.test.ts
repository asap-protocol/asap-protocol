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

async function listenLoopback(handler: http.RequestListener): Promise<{
  server: http.Server;
  port: number;
}> {
  const server = http.createServer(handler);
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address() as AddressInfo;
  return { server, port };
}

async function closeServer(server: http.Server): Promise<void> {
  await new Promise<void>((resolve, reject) =>
    server.close((err) => (err ? reject(err) : resolve()))
  );
}

const pinLoopback = async (): Promise<{ valid: true; ips: string[] }> => ({
  valid: true,
  ips: ['127.0.0.1'],
});

describe('fetchAllowlistedUrl', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('connects to the pinned IP even if DNS would rebind the hostname', async () => {
    const { server, port } = await listenLoopback((_req, res) => {
      res.writeHead(200, { 'content-type': 'text/plain' });
      res.end('ok');
    });

    try {
      const result = await fetchAllowlistedUrl(
        `http://rebind.example.invalid:${port}/`,
        pinLoopback,
        2000
      );
      expect(isPinnedFetchBlocked(result)).toBe(false);
      if (!isPinnedFetchBlocked(result)) {
        expect(result).toEqual({ ok: true, status: 200 });
      }
    } finally {
      await closeServer(server);
    }
  });

  it('re-validates redirect targets and blocks private Location hops', async () => {
    const { server, port } = await listenLoopback((req, res) => {
      if (req.url === '/start') {
        res.writeHead(302, { Location: 'http://127.0.0.1/secret' });
        res.end();
        return;
      }
      res.writeHead(200);
      res.end('should-not-reach');
    });

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
      await closeServer(server);
    }
  });

  it('destroys the hop socket after headers so a streaming body cannot pin the isolate', async () => {
    let serverSawClose = false;
    const { server, port } = await listenLoopback((_req, res) => {
      res.writeHead(200, { 'content-type': 'text/plain' });
      const interval = setInterval(() => {
        res.write('x'.repeat(16 * 1024));
      }, 20);
      res.on('close', () => {
        clearInterval(interval);
        serverSawClose = true;
      });
    });

    try {
      const started = Date.now();
      const result = await fetchAllowlistedUrl(
        `http://rebind.example.invalid:${port}/`,
        pinLoopback,
        2000
      );
      expect(isPinnedFetchBlocked(result)).toBe(false);
      if (!isPinnedFetchBlocked(result)) {
        expect(result).toEqual({ ok: true, status: 200 });
      }
      expect(Date.now() - started).toBeLessThan(500);
      await vi.waitFor(() => {
        expect(serverSawClose).toBe(true);
      });
    } finally {
      await closeServer(server);
    }
  });

  it('applies timeoutMs to the whole redirect walk, not each hop', async () => {
    const hopDelayMs = 250;
    const { server, port } = await listenLoopback((req, res) => {
      const delay = () => {
        if (req.url === '/a') {
          res.writeHead(302, { Location: '/b' });
          res.end();
          return;
        }
        res.writeHead(200);
        res.end('ok');
      };
      setTimeout(delay, hopDelayMs);
    });

    try {
      const result = await fetchAllowlistedUrl(
        `http://rebind.example.invalid:${port}/a`,
        pinLoopback,
        350
      );
      expect(isPinnedFetchBlocked(result)).toBe(false);
      if (!isPinnedFetchBlocked(result)) {
        expect(result).toEqual({ ok: false, status: 0 });
      }
    } finally {
      await closeServer(server);
    }
  });

  it('blocks when the allowlist omits pinned IPs', async () => {
    const result = await fetchAllowlistedUrl(
      'http://rebind.example.invalid/',
      async () => ({ valid: true }),
      1000
    );
    expect(isPinnedFetchBlocked(result)).toBe(true);
    if (isPinnedFetchBlocked(result)) {
      expect(result.error).toContain('pinned IPs');
    }
  });

  it('returns an error after too many redirect hops', async () => {
    const { server, port } = await listenLoopback((_req, res) => {
      res.writeHead(302, { Location: '/loop' });
      res.end();
    });

    try {
      const result = await fetchAllowlistedUrl(
        `http://rebind.example.invalid:${port}/loop`,
        pinLoopback,
        2000
      );
      expect(isPinnedFetchBlocked(result)).toBe(true);
      if (isPinnedFetchBlocked(result)) {
        expect(result.error).toContain('Too many redirects');
      }
    } finally {
      await closeServer(server);
    }
  });

  it('blocks an http Location hop when the validator is HTTPS-only', async () => {
    const { server, port } = await listenLoopback((req, res) => {
      if (req.url === '/start') {
        res.writeHead(302, { Location: 'http://rebind.example.invalid/next' });
        res.end();
        return;
      }
      res.writeHead(200);
      res.end('should-not-reach');
    });

    try {
      const result = await fetchAllowlistedUrl(
        `http://rebind.example.invalid:${port}/start`,
        async (url) => {
          if (url.startsWith('http://') && !url.includes('/start')) {
            return { valid: false, error: 'URL must use HTTPS only.' };
          }
          return { valid: true, ips: ['127.0.0.1'] };
        },
        2000
      );
      expect(isPinnedFetchBlocked(result)).toBe(true);
      if (isPinnedFetchBlocked(result)) {
        expect(result.error).toContain('HTTPS only');
      }
    } finally {
      await closeServer(server);
    }
  });

  it('can probe with HEAD while still pinning connect()', async () => {
    let seenMethod: string | undefined;
    const { server, port } = await listenLoopback((req, res) => {
      seenMethod = req.method;
      res.writeHead(200);
      res.end();
    });

    try {
      const result = await fetchAllowlistedUrl(
        `http://rebind.example.invalid:${port}/`,
        pinLoopback,
        2000,
        'HEAD'
      );
      expect(seenMethod).toBe('HEAD');
      expect(isPinnedFetchBlocked(result)).toBe(false);
      if (!isPinnedFetchBlocked(result)) {
        expect(result).toEqual({ ok: true, status: 200 });
      }
    } finally {
      await closeServer(server);
    }
  });
});
