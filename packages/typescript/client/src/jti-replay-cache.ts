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
  /**
   * Record `jti` under `partitionKey`. Return `false` when it is already
   * recorded (a replay) or blank. `expiresAt` is the token's `exp`, in seconds
   * since the epoch: keep the record at least until then, so a token cannot be
   * replayed after its record is gone.
   */
  checkAndRecord(partitionKey: string, jti: string, expiresAt?: number): boolean | Promise<boolean>;
}

export interface InMemoryJtiReplayCacheOptions {
  /** How long a recorded `jti` blocks reuse at least; longer when the token's `exp` is later. Default 90 s. */
  readonly ttlSeconds?: number;
  /**
   * Maximum number of records. A live record is never evicted: expired
   * records make room first, and when every record is live,
   * `checkAndRecord` throws {@link JtiReplayCacheFullError}. Default 10 000.
   */
  readonly maxSize?: number;
  /** Maximum number of live records under one partition key (one agent), so one agent cannot fill the cache. Default 1 000. */
  readonly maxPerPartition?: number;
  /** Clock in seconds since the epoch. Default: the system clock. */
  readonly nowSeconds?: () => number;
}

/**
 * Thrown by `InMemoryJtiReplayCache.checkAndRecord` when there is no room for
 * a new record and every record is live. The token may
 * be fine, so refuse it as a temporary failure, not as a bad token. The
 * message holds no `jti` or partition key.
 */
export class JtiReplayCacheFullError extends Error {
  /** Seconds until enough live records expire to make room (at least 1). */
  readonly retryAfterSeconds: number;

  constructor(retryAfterSeconds: number) {
    super(`JTI replay cache is full of live records; retry after ${retryAfterSeconds} s`);
    this.name = "JtiReplayCacheFullError";
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

// Same defaults as Python `JtiReplayCache` (src/asap/auth/agent_jwt.py), plus the per-partition cap.
const DEFAULT_REPLAY_TTL_SECONDS = 90;
const DEFAULT_REPLAY_MAX_SIZE = 10_000;
const DEFAULT_REPLAY_MAX_PER_PARTITION = 1_000;

function systemNowSeconds(): number {
  return Date.now() / 1000;
}

function positiveInteger(name: string, value: number): number {
  if (!Number.isInteger(value) || value < 1) {
    throw new TypeError(`InMemoryJtiReplayCache ${name} must be a positive integer; received ${value}`);
  }
  return value;
}

function secondsUntilFirstExpiry(scope: Iterable<ReadonlyMap<string, number>>, now: number): number {
  let first = Infinity;
  for (const records of scope) {
    for (const expiry of records.values()) first = Math.min(first, expiry);
  }
  return Math.max(1, Math.ceil(first - now));
}

/** In-memory {@link JtiReplayCache} for one process. It never evicts a live record. */
export class InMemoryJtiReplayCache implements JtiReplayCache {
  /** Partition key → `jti` → expiry, in seconds since the epoch. */
  readonly #partitions = new Map<string, Map<string, number>>();
  #size = 0;
  readonly #ttlSeconds: number;
  readonly #maxSize: number;
  readonly #maxPerPartition: number;
  readonly #nowSeconds: () => number;

  constructor(options: InMemoryJtiReplayCacheOptions = {}) {
    this.#ttlSeconds = options.ttlSeconds ?? DEFAULT_REPLAY_TTL_SECONDS;
    this.#maxSize = positiveInteger("maxSize", options.maxSize ?? DEFAULT_REPLAY_MAX_SIZE);
    this.#maxPerPartition = positiveInteger("maxPerPartition", options.maxPerPartition ?? DEFAULT_REPLAY_MAX_PER_PARTITION);
    this.#nowSeconds = options.nowSeconds ?? systemNowSeconds;
  }

  checkAndRecord(partitionKey: string, jti: string, expiresAt = 0): boolean {
    if (jti.trim() === "") {
      return false;
    }
    const now = this.#nowSeconds();
    const recorded = this.#partitions.get(partitionKey)?.get(jti);
    if (recorded !== undefined && recorded > now) {
      return false;
    }
    if (recorded === undefined) {
      this.#makeRoom(partitionKey, now);
      this.#size += 1;
    }
    const records = this.#partitions.get(partitionKey) ?? new Map<string, number>();
    records.set(jti, Math.max(now + this.#ttlSeconds, expiresAt));
    this.#partitions.set(partitionKey, records);
    return true;
  }

  #fullScopes(partitionKey: string): ReadonlyMap<string, number>[][] {
    const partition = this.#partitions.get(partitionKey);
    const scopes: ReadonlyMap<string, number>[][] = [];
    if (this.#size >= this.#maxSize) scopes.push([...this.#partitions.values()]);
    if (partition !== undefined && partition.size >= this.#maxPerPartition) scopes.push([partition]);
    return scopes;
  }

  /** Prune expired records when a limit is reached; throw when it is still reached. */
  #makeRoom(partitionKey: string, now: number): void {
    if (this.#fullScopes(partitionKey).length === 0) return;
    this.#pruneExpired(now);
    const full = this.#fullScopes(partitionKey);
    if (full.length === 0) return;
    throw new JtiReplayCacheFullError(Math.max(...full.map((scope) => secondsUntilFirstExpiry(scope, now))));
  }

  #pruneExpired(now: number): void {
    for (const [partitionKey, records] of this.#partitions) {
      for (const [jti, expiry] of records) {
        if (expiry > now) continue;
        records.delete(jti);
        this.#size -= 1;
      }
      if (records.size === 0) this.#partitions.delete(partitionKey);
    }
  }
}
