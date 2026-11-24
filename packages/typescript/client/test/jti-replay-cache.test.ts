import { describe, expect, it } from "vitest";

import { InMemoryJtiReplayCache } from "../src/index.js";

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

  it("refuses a blank jti", () => {
    const cache = new InMemoryJtiReplayCache();

    expect(cache.checkAndRecord("agent-1", "")).toBe(false);
    expect(cache.checkAndRecord("agent-1", "  ")).toBe(false);
  });

  it("evicts the oldest entries first when it is full", () => {
    const clock = manualClock(1_700_000_000);
    const cache = new InMemoryJtiReplayCache({ maxSize: 2, nowSeconds: clock.now });

    cache.checkAndRecord("agent-1", "jwt_a");
    clock.advance(1);
    cache.checkAndRecord("agent-1", "jwt_b");
    clock.advance(1);
    cache.checkAndRecord("agent-1", "jwt_c");

    expect(cache.checkAndRecord("agent-1", "jwt_c")).toBe(false);
    expect(cache.checkAndRecord("agent-1", "jwt_a")).toBe(true);
  });
});
