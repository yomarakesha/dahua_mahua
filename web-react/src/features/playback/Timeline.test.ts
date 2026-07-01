/**
 * Unit tests for Timeline pure math helpers.
 *
 * TDD protocol: this file was written BEFORE Timeline.tsx existed (RED phase).
 * Tests turn GREEN once epochToPercent / percentToEpoch are exported from Timeline.tsx.
 *
 * Component render / keyboard tests live in Timeline.test.tsx (JSX requires .tsx).
 */
import { describe, it, expect } from "vitest";
import { epochToPercent, percentToEpoch } from "./Timeline";

// ── Fixture day: 2025-06-30 UTC+0 ─────────────────────────────────────────────

/** 2025-06-30T00:00:00Z */
const DAY_START = 1_751_241_600;
/** 2025-07-01T00:00:00Z  (exactly 86 400 s later) */
const DAY_END = DAY_START + 86_400;

// ── epochToPercent ────────────────────────────────────────────────────────────

describe("epochToPercent", () => {
  it("returns 0 at dayStart", () => {
    expect(epochToPercent(DAY_START, DAY_START, DAY_END)).toBe(0);
  });

  it("returns 100 at dayEnd", () => {
    expect(epochToPercent(DAY_END, DAY_START, DAY_END)).toBe(100);
  });

  it("returns 50 at the midpoint (12:00:00 UTC+0)", () => {
    const mid = DAY_START + 43_200; // 12 h into the day
    expect(epochToPercent(mid, DAY_START, DAY_END)).toBe(50);
  });

  it("returns 25 at the 6h mark", () => {
    expect(epochToPercent(DAY_START + 21_600, DAY_START, DAY_END)).toBe(25);
  });

  it("returns 12.5 at the 3h mark (first non-zero axis label)", () => {
    expect(epochToPercent(DAY_START + 10_800, DAY_START, DAY_END)).toBe(12.5);
  });
});

// ── percentToEpoch ────────────────────────────────────────────────────────────

describe("percentToEpoch", () => {
  it("returns dayStart at 0%", () => {
    expect(percentToEpoch(0, DAY_START, DAY_END)).toBe(DAY_START);
  });

  it("returns dayEnd at 100%", () => {
    expect(percentToEpoch(100, DAY_START, DAY_END)).toBe(DAY_END);
  });

  it("returns the midpoint at 50%", () => {
    expect(percentToEpoch(50, DAY_START, DAY_END)).toBe(DAY_START + 43_200);
  });
});

// ── Round-trip ────────────────────────────────────────────────────────────────

describe("epochToPercent / percentToEpoch round-trip", () => {
  const testEpochs = [
    DAY_START,
    DAY_START + 3_600,     // 1 h
    DAY_START + 43_200,    // 12 h
    DAY_START + 75_600,    // 21 h
    DAY_END,
  ];

  it.each(testEpochs)("round-trips epoch %i", (epoch) => {
    const pct = epochToPercent(epoch, DAY_START, DAY_END);
    const back = percentToEpoch(pct, DAY_START, DAY_END);
    expect(back).toBeCloseTo(epoch, 5);
  });
});

// ── Non-86400 day (e.g. hypothetical 25-hour NVR day) ─────────────────────────

describe("non-86400 day duration", () => {
  const START = 1_000_000;
  const END = START + 90_000; // 25 h = 90 000 s

  it("midpoint of a 25h day maps to 50%", () => {
    const mid = START + 45_000;
    expect(epochToPercent(mid, START, END)).toBe(50);
  });

  it("percentToEpoch(50%) returns the midpoint of a 25h day", () => {
    expect(percentToEpoch(50, START, END)).toBe(START + 45_000);
  });

  it("does NOT assume 86 400 s duration — 12 h into a 25h day ≠ 50%", () => {
    const twelveH = START + 43_200;
    const pct = epochToPercent(twelveH, START, END);
    // ~48.0 %, not 50 %
    expect(pct).not.toBe(50);
    expect(pct).toBeCloseTo((43_200 / 90_000) * 100, 5);
  });
});

// ── Axis label positions (UTC+0, 24h day) ─────────────────────────────────────
//
// The Timeline renders hour labels at 0, 3, 6, 9, 12, 15, 18, 21.
// Position formula (from task spec): ((nvrHour × 3600) / dayDuration) × 100
// which equals epochToPercent(dayStart + nvrHour×3600, dayStart, dayEnd).

describe("axis label positions for a 24-hour day at UTC+0", () => {
  const AXIS_HOURS = [0, 3, 6, 9, 12, 15, 18, 21] as const;

  it.each(AXIS_HOURS)("hour %i is at the correct percentage", (hour) => {
    const elapsedSec = hour * 3_600;
    const expectedPct = (elapsedSec / 86_400) * 100;
    const epoch = DAY_START + elapsedSec;
    expect(epochToPercent(epoch, DAY_START, DAY_END)).toBeCloseTo(expectedPct, 10);
  });

  it("0h is at exactly 0%", () => {
    expect(epochToPercent(DAY_START, DAY_START, DAY_END)).toBe(0);
  });

  it("12h is at exactly 50%", () => {
    expect(epochToPercent(DAY_START + 43_200, DAY_START, DAY_END)).toBe(50);
  });

  it("3h is at exactly 12.5%", () => {
    expect(epochToPercent(DAY_START + 10_800, DAY_START, DAY_END)).toBe(12.5);
  });
});
