/**
 * Batch-2 #1 — self-heal backoff schedule. The stall-heal reconnect uses a
 * growing backoff (1s → 2s → 4s, capped) plus up to 1s of jitter so a wall of
 * tiles healing at once doesn't reconnect in lockstep. Pure + unit-testable.
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import { healBackoffMs } from "./MsePlayer";

afterEach(() => vi.restoreAllMocks());

describe("healBackoffMs", () => {
  it("grows 1s → 2s → 4s and caps at 4s (jitter zeroed)", () => {
    vi.spyOn(Math, "random").mockReturnValue(0);
    expect(healBackoffMs(1)).toBe(1000);
    expect(healBackoffMs(2)).toBe(2000);
    expect(healBackoffMs(3)).toBe(4000);
    expect(healBackoffMs(4)).toBe(4000); // capped
    expect(healBackoffMs(9)).toBe(4000); // still capped
  });

  it("adds up to ~1s of jitter on top of the base", () => {
    vi.spyOn(Math, "random").mockReturnValue(0.5);
    expect(healBackoffMs(1)).toBe(1500); // 1000 base + 500 jitter
    vi.spyOn(Math, "random").mockReturnValue(0.999);
    expect(healBackoffMs(1)).toBeGreaterThan(1990);
    expect(healBackoffMs(1)).toBeLessThan(2000);
  });

  it("treats attempt 0 / negatives as the first step (never below 1s base)", () => {
    vi.spyOn(Math, "random").mockReturnValue(0);
    expect(healBackoffMs(0)).toBe(1000);
    expect(healBackoffMs(-5)).toBe(1000);
  });
});
