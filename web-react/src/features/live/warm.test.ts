/**
 * Warm-set computation (current page + next page, deduped).
 */
import { describe, it, expect } from "vitest";
import { computeWarmIds } from "./warm";

const cams = (n: number) => Array.from({ length: n }, (_, i) => ({ id: `c${i}` }));

describe("computeWarmIds", () => {
  it("returns current page + next page ids", () => {
    // 10 cams, 4 per page → 3 pages. Page 0 warms cams 0-3 (current) + 4-7 (next).
    expect(computeWarmIds(cams(10), 0, 4, 3)).toEqual([
      "c0", "c1", "c2", "c3", "c4", "c5", "c6", "c7",
    ]);
  });

  it("wraps the next page around from the last page", () => {
    // Page 2 (cams 8-9) + next wraps to page 0 (cams 0-3).
    expect(computeWarmIds(cams(10), 2, 4, 3)).toEqual([
      "c8", "c9", "c0", "c1", "c2", "c3",
    ]);
  });

  it("single page: no next page, no duplicates", () => {
    expect(computeWarmIds(cams(3), 0, 4, 1)).toEqual(["c0", "c1", "c2"]);
  });

  it("empty inputs are safe", () => {
    expect(computeWarmIds([], 0, 4, 1)).toEqual([]);
    expect(computeWarmIds(cams(3), 0, 0, 1)).toEqual([]);
  });

  it("dedupes when a two-page wall wraps its next page onto itself", () => {
    // 5 cams, 4/page → 2 pages. Page 1 = cam 4; next wraps to page 0 = 0-3.
    // No id repeats here, but the Set guards a full page repeat.
    expect(computeWarmIds(cams(5), 1, 4, 2)).toEqual(["c4", "c0", "c1", "c2", "c3"]);
  });
});
