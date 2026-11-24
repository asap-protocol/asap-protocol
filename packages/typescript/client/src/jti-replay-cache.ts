/**
 * Replay guard for Agent JWT `jti` values. An Agent JWT is single-use: the
 * verifier records its `jti` under the agent id, and a second use inside the
 * window is a replay. Same contract as Python `JtiReplayCacheProtocol.check_and_record`
 * (`src/asap/auth/jti_replay_cache.py`). A multi-instance server needs a shared
 * store (for example Redis) behind this interface.
 *
 * @example
 * ```ts
 * const cache: JtiReplayCache = new InMemoryJtiReplayCache();
 * cache.checkAndRecord("agent-1", "jwt_01H..."); // true, then false on reuse
 * ```
 */
export interface JtiReplayCache {
  /** Record `jti` under `partitionKey`. Return `false` when it is already recorded (a replay) or blank. */
  checkAndRecord(partitionKey: string, jti: string): boolean | Promise<boolean>;
}

export interface InMemoryJtiReplayCacheOptions {
  /** How long a recorded `jti` blocks reuse. Default 90 s, longer than the 60 s Agent JWT lifetime. */
  readonly ttlSeconds?: number;
  /** Maximum number of entries. When full, the oldest entries go first. Default 10 000. */
  readonly maxSize?: number;
  /** Clock in seconds since the epoch. Default: the system clock. */
  readonly nowSeconds?: () => number;
}

// Same defaults as Python `JtiReplayCache` (src/asap/auth/agent_jwt.py).
const DEFAULT_REPLAY_TTL_SECONDS = 90;
const DEFAULT_REPLAY_MAX_SIZE = 10_000;

function systemNowSeconds(): number {
  return Date.now() / 1000;
}

/** In-memory {@link JtiReplayCache} for one process. */
export class InMemoryJtiReplayCache implements JtiReplayCache {
  readonly #expiryByKey = new Map<string, number>();
  readonly #ttlSeconds: number;
  readonly #maxSize: number;
  readonly #nowSeconds: () => number;

  constructor(options: InMemoryJtiReplayCacheOptions = {}) {
    this.#ttlSeconds = options.ttlSeconds ?? DEFAULT_REPLAY_TTL_SECONDS;
    this.#maxSize = options.maxSize ?? DEFAULT_REPLAY_MAX_SIZE;
    this.#nowSeconds = options.nowSeconds ?? systemNowSeconds;
  }

  checkAndRecord(partitionKey: string, jti: string): boolean {
    if (jti.trim() === "") {
      return false;
    }
    const now = this.#nowSeconds();
    this.#pruneExpired(now);
    // A JSON pair cannot collide the way "a:b" + "c" and "a" + "b:c" would.
    const key = JSON.stringify([partitionKey, jti]);
    if (this.#expiryByKey.has(key)) {
      return false;
    }
    this.#expiryByKey.set(key, now + this.#ttlSeconds);
    this.#evictOldest();
    return true;
  }

  #pruneExpired(now: number): void {
    for (const [key, expiry] of this.#expiryByKey) {
      if (expiry <= now) {
        this.#expiryByKey.delete(key);
      }
    }
  }

  // Map iteration follows insertion order, and every entry has the same TTL,
  // so the first keys are the ones that expire first.
  #evictOldest(): void {
    for (const key of this.#expiryByKey.keys()) {
      if (this.#expiryByKey.size <= this.#maxSize) {
        return;
      }
      this.#expiryByKey.delete(key);
    }
  }
}
