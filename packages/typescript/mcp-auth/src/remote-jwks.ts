import { createLocalJWKSet, errors, type JWK } from "jose";

/** How a `jwksUri` key source fetches and caches. The defaults suit a production issuer. */
export interface RemoteJwksOptions {
  /** The fetch to use. Default: `globalThis.fetch`. */
  readonly fetch?: typeof fetch;
  /** Abort a fetch, body included, after this time. Default: 5 s. */
  readonly timeoutMs?: number;
  /** Refuse a JWKS document larger than this. Default: 64 KiB. */
  readonly maxBytes?: number;
  /** Keys older than this are not used; the next token fetches again. Default: 10 min. */
  readonly maxAgeMs?: number;
  /** At most one fetch per this interval, so unknown `kid` values cannot amplify fetches. Default: 30 s. */
  readonly refreshCooldownMs?: number;
  /** Clock in milliseconds, for tests. Default: `Date.now`. */
  readonly nowMs?: () => number;
}

type KeySet = ReturnType<typeof createLocalJWKSet>;

const MAX_REDIRECTS = 3;
const REDIRECT_STATUSES = [301, 302, 303, 307, 308];

/** A failed fetch is an infrastructure failure, not a token failure: not a `JOSEError`, so the answer is HTTP 500. */
class JwksUnavailableError extends Error {
  constructor(url: URL, reason: string, options?: ErrorOptions) {
    super(`JWKS from ${url.host} is unavailable: ${reason}`, options);
    this.name = "JwksUnavailableError";
  }
}

function parseJwksUri(jwksUri: string): URL {
  const url = URL.canParse(jwksUri) ? new URL(jwksUri) : undefined;
  if (url === undefined || url.protocol !== "https:" || url.username !== "" || url.password !== "" || url.hash !== "") {
    throw new TypeError(`jwksUri must be an https URL with no credentials and no fragment; received ${JSON.stringify(jwksUri)}`);
  }
  return url;
}

async function readLimited(url: URL, response: Response, maxBytes: number): Promise<string> {
  if (Number(response.headers.get("content-length")) > maxBytes) throw new JwksUnavailableError(url, `larger than ${maxBytes} bytes`);
  const reader = response.body?.getReader();
  const decoder = new TextDecoder();
  let text = "";
  let total = 0;
  for (let chunk = await reader?.read(); chunk !== undefined && !chunk.done; chunk = await reader?.read()) {
    total += chunk.value.byteLength;
    if (total > maxBytes) {
      await reader?.cancel();
      throw new JwksUnavailableError(url, `larger than ${maxBytes} bytes`);
    }
    text += decoder.decode(chunk.value, { stream: true });
  }
  return text + decoder.decode();
}

function publicKeySet(url: URL, text: string, isPublicKey: (key: JWK) => boolean): KeySet {
  let document: unknown;
  try {
    document = JSON.parse(text);
  } catch (cause) {
    throw new JwksUnavailableError(url, "the body is not JSON", { cause });
  }
  const keys: unknown = (document as { keys?: unknown } | null)?.keys;
  if (!Array.isArray(keys)) throw new JwksUnavailableError(url, "the body has no keys list");
  return createLocalJWKSet({ keys: (keys as JWK[]).filter(isPublicKey) });
}

/**
 * A key source for `jwtVerify` that fetches an issuer's JWKS. The fetch is
 * HTTPS only, follows a redirect only on the same origin (at most three),
 * has a size limit and a timeout, and caches keys for at most `maxAgeMs`.
 * An unknown `kid` refreshes the keys, so a rotated key verifies. At most
 * one fetch runs per `refreshCooldownMs`, and concurrent requests share it,
 * so random `kid` values cannot amplify fetches. A token never adds a fetch
 * target: `jku`, `x5u`, and the issuer URL are not fetched. It does not pin
 * the resolved IP address.
 */
export function createRemoteJwksSource(jwksUri: string, isPublicKey: (key: JWK) => boolean, options: RemoteJwksOptions = {}): KeySet {
  const url = parseJwksUri(jwksUri);
  const fetchJwks = options.fetch ?? globalThis.fetch;
  const nowMs = options.nowMs ?? Date.now;
  const timeoutMs = options.timeoutMs ?? 5_000;
  const maxBytes = options.maxBytes ?? 65_536;
  const maxAgeMs = options.maxAgeMs ?? 600_000;
  const cooldownMs = options.refreshCooldownMs ?? 30_000;
  let cached: { keys: KeySet; fetchedAt: number } | undefined;
  let lastAttempt = Number.NEGATIVE_INFINITY;
  let inflight: Promise<KeySet> | undefined;

  async function download(): Promise<KeySet> {
    const signal = AbortSignal.timeout(timeoutMs);
    let target = url;
    for (let hop = 0; hop <= MAX_REDIRECTS; hop += 1) {
      const response = await fetchJwks(target, { redirect: "manual", signal, headers: { accept: "application/json" } });
      if (!REDIRECT_STATUSES.includes(response.status)) {
        if (!response.ok) throw new JwksUnavailableError(url, `HTTP ${response.status}`);
        return publicKeySet(url, await readLimited(url, response, maxBytes), isPublicKey);
      }
      target = new URL(response.headers.get("location") ?? "", target);
      if (target.origin !== url.origin) throw new JwksUnavailableError(url, "redirect to another origin refused");
    }
    throw new JwksUnavailableError(url, `more than ${MAX_REDIRECTS} redirects`);
  }

  function refresh(): Promise<KeySet> {
    inflight ??= (async () => {
      lastAttempt = nowMs();
      try {
        const keys = await download().catch((cause: unknown) => {
          throw cause instanceof JwksUnavailableError ? cause : new JwksUnavailableError(url, "fetch failed", { cause });
        });
        cached = { keys, fetchedAt: nowMs() };
        return keys;
      } finally {
        inflight = undefined;
      }
    })();
    return inflight;
  }

  const mayFetch = () => inflight !== undefined || nowMs() - lastAttempt >= cooldownMs;

  async function currentKeys(): Promise<KeySet> {
    if (cached !== undefined && nowMs() - cached.fetchedAt < maxAgeMs) return cached.keys;
    if (!mayFetch()) throw new JwksUnavailableError(url, "the last fetch failed; waiting for the refresh cooldown");
    return refresh();
  }

  // An unknown kid may be a rotated key: refresh once, but never more often than the cooldown allows.
  return async (protectedHeader, token) => {
    const keys = await currentKeys();
    try {
      return await keys(protectedHeader, token);
    } catch (error) {
      if (!(error instanceof errors.JWKSNoMatchingKey) || !mayFetch()) throw error;
      return (await refresh())(protectedHeader, token);
    }
  };
}
