import { describe, expect, it } from "vitest";

import { InMemoryJtiReplayCache, JtiReplayCacheFullError } from "../src/index.js";

/** A clock the test moves by hand, in seconds since the epoch. */
function manualClock(start: number): { now: () => number; advance: (seconds: number) => void } {
  let current = start;
  return { now: () => current, advance: (seconds) => (current += seconds) };
}

describe("InMemoryJtiReplayCache", () => {
  it("accepts the first use of a jti and refuses the second", () => {
    const cache = new InMemoryJtiReplayCache();

    expect(cache.checkAndRecord("agent-1", "jwt_a")).toBe(true);
    expect(cache.checkAndRecord("agent-1", "jwt_a")).toBe(false);
  });

  it("keeps partitions apart, so one agent's jti does not block another agent", () => {
    const cache = new InMemoryJtiReplayCache();

    expect(cache.checkAndRecord("agent-1", "jwt_a")).toBe(true);
    expect(cache.checkAndRecord("agent-2", "jwt_a")).toBe(true);
  });

  it("does not confuse partition and jti boundaries", () => {
    const cache = new InMemoryJtiReplayCache();

    expect(cache.checkAndRecord("a:b", "c")).toBe(true);
    expect(cache.checkAndRecord("a", "b:c")).toBe(true);
  });

  it("allows the jti again once the window has passed", () => {
    const clock = manualClock(1_700_000_000);
    const cache = new InMemoryJtiReplayCache({ ttlSeconds: 90, nowSeconds: clock.now });

    expect(cache.checkAndRecord("agent-1", "jwt_a")).toBe(true);
    clock.advance(89);
    expect(cache.checkAndRecord("agent-1", "jwt_a")).toBe(false);
    clock.advance(1);
    expect(cache.checkAndRecord("agent-1", "jwt_a")).toBe(true);
  });

  // A record must not expire before the token does.
  it("keeps a jti until the token's exp when that is later than the window", () => {
    const clock = manualClock(1_700_000_000);
    const cache = new InMemoryJtiReplayCache({ ttlSeconds: 90, nowSeconds: clock.now });

    expect(cache.checkAndRecord("agent-1", "jwt_a", 1_700_000_120)).toBe(true);
    clock.advance(119);
    expect(cache.checkAndRecord("agent-1", "jwt_a", 1_700_000_120)).toBe(false);
    clock.advance(1);
    expect(cache.checkAndRecord("agent-1", "jwt_a", 1_700_000_120)).toBe(true);
  });

  it("keeps the window when the token's exp is earlier", () => {
    const clock = manualClock(1_700_000_000);
    const cache = new InMemoryJtiReplayCache({ ttlSeconds: 90, nowSeconds: clock.now });

    cache.checkAndRecord("agent-1", "jwt_a", 1_700_000_010);
    clock.advance(89);

    expect(cache.checkAndRecord("agent-1", "jwt_a")).toBe(false);
  });

  it("refuses a blank jti", () => {
    const cache = new InMemoryJtiReplayCache();

    expect(cache.checkAndRecord("agent-1", "")).toBe(false);
    expect(cache.checkAndRecord("agent-1", "  ")).toBe(false);
  });

  // Evicting live records would let one agent push out another agent's jti and replay it.
  it("never evicts a live jti: with maxSize 2 it refuses a third one, and the first is still a replay", () => {
    const clock = manualClock(1_700_000_000);
    const cache = new InMemoryJtiReplayCache({ maxSize: 2, nowSeconds: clock.now });

    cache.checkAndRecord("victim", "jwt_a");
    clock.advance(1);
    cache.checkAndRecord("attacker", "jwt_b");

    expect(() => cache.checkAndRecord("attacker", "jwt_c")).toThrow(JtiReplayCacheFullError);
    expect(cache.checkAndRecord("victim", "jwt_a")).toBe(false);
  });

  it("tells how long until the first live record expires", () => {
    const clock = manualClock(1_700_000_000);
    const cache = new InMemoryJtiReplayCache({ maxSize: 1, ttlSeconds: 90, nowSeconds: clock.now });
    cache.checkAndRecord("agent-1", "jwt_a");
    clock.advance(30.5);

    const error = captureFullError(() => cache.checkAndRecord("agent-2", "jwt_b"));

    expect(error.retryAfterSeconds).toBe(60);
    expect(error.message).not.toContain("jwt_");
  });

  it("lets expired records make room", () => {
    const clock = manualClock(1_700_000_000);
    const cache = new InMemoryJtiReplayCache({ maxSize: 2, ttlSeconds: 90, nowSeconds: clock.now });
    cache.checkAndRecord("agent-1", "jwt_a");
    cache.checkAndRecord("agent-1", "jwt_b");
    clock.advance(90);

    expect(cache.checkAndRecord("agent-1", "jwt_c")).toBe(true);
    expect(cache.checkAndRecord("agent-1", "jwt_d")).toBe(true);
  });

  it("caps the live records of one partition, so one agent cannot fill the cache", () => {
    const cache = new InMemoryJtiReplayCache({ maxSize: 10, maxPerPartition: 2 });
    cache.checkAndRecord("agent-1", "jwt_a");
    cache.checkAndRecord("agent-1", "jwt_b");

    expect(() => cache.checkAndRecord("agent-1", "jwt_c")).toThrow(JtiReplayCacheFullError);
    expect(cache.checkAndRecord("agent-2", "jwt_c")).toBe(true);
  });

  it.each([
    ["maxSize", { maxSize: 0 }],
    ["maxPerPartition", { maxPerPartition: 1.5 }],
  ])("refuses a %s that is not a positive integer", (name, options) => {
    expect(() => new InMemoryJtiReplayCache(options)).toThrow(name);
  });
});

function captureFullError(record: () => void): JtiReplayCacheFullError {
  try {
    record();
  } catch (error) {
    expect(error).toBeInstanceOf(JtiReplayCacheFullError);
    return error as JtiReplayCacheFullError;
  }
  return expect.unreachable("the cache accepted the record");
}
