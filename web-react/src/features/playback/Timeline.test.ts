/**
 * Unit tests for Timeline pure math helpers.
 *
 * TDD protocol: this file was written BEFORE Timeline.tsx existed (RED phase).
 * Tests turn GREEN once epochToPercent / percentToEpoch are exported from Timeline.tsx.
 *
 * Component render / keyboard tests live in Timeline.test.tsx (JSX requires .tsx).
 */
import { describe, it, expect } from "vitest";
import {
  epochToPercent,
  percentToEpoch,
  zoomSpanSeconds,
  zoomScale,
  clampZoomIndex,
  chooseTickInterval,
  formatSpanLabel,
  formatDurationLabel,
  MAX_ZOOM_INDEX,
  ZOOM_SPANS_SECONDS,
} from "./Timeline";
import { clampClipRange, MAX_CLIP_SECONDS } from "./playback-utils";

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

// ── Zoom span math ────────────────────────────────────────────────────────────

describe("zoomSpanSeconds", () => {
  it("index 0 fits the whole day", () => {
    expect(zoomSpanSeconds(0, 86_400)).toBe(86_400);
  });

  it("index 1 is the first fixed step (12h)", () => {
    expect(zoomSpanSeconds(1, 86_400)).toBe(ZOOM_SPANS_SECONDS[0]);
    expect(zoomSpanSeconds(1, 86_400)).toBe(43_200);
  });

  it("deepest index is the 1-minute window", () => {
    expect(zoomSpanSeconds(MAX_ZOOM_INDEX, 86_400)).toBe(60);
  });

  it("clamps an over-deep index to the last step", () => {
    expect(zoomSpanSeconds(999, 86_400)).toBe(60);
  });

  it("never exceeds the real day duration (short day)", () => {
    // A 1h day can't zoom OUT to a 12h window.
    expect(zoomSpanSeconds(1, 3_600)).toBe(3_600);
  });
});

describe("zoomScale", () => {
  it("is 1 at full-day fit", () => {
    expect(zoomScale(0, 86_400)).toBe(1);
  });

  it("is dayDuration / span for a zoomed step", () => {
    // 1h window on an 86 400 s day → 24× wider track.
    const oneHourIndex = ZOOM_SPANS_SECONDS.indexOf(3_600) + 1;
    expect(zoomScale(oneHourIndex, 86_400)).toBe(24);
  });
});

describe("clampZoomIndex", () => {
  it("clamps below 0 → 0", () => {
    expect(clampZoomIndex(-3)).toBe(0);
  });
  it("clamps above MAX → MAX", () => {
    expect(clampZoomIndex(MAX_ZOOM_INDEX + 5)).toBe(MAX_ZOOM_INDEX);
  });
  it("rounds fractional indices", () => {
    expect(clampZoomIndex(2.4)).toBe(2);
  });
});

describe("chooseTickInterval", () => {
  it("uses 3h ticks for a full day", () => {
    expect(chooseTickInterval(86_400)).toBe(3 * 3_600);
  });
  it("uses 15m ticks for a 1h span", () => {
    expect(chooseTickInterval(3_600)).toBe(900);
  });
  it("uses 1m ticks at the deepest zoom", () => {
    expect(chooseTickInterval(60)).toBe(60);
  });
  it("is monotonic non-increasing as span shrinks", () => {
    const spans = [86_400, 43_200, 21_600, 10_800, 3_600, 1_800, 900, 300, 60];
    const intervals = spans.map(chooseTickInterval);
    for (let i = 1; i < intervals.length; i++) {
      expect(intervals[i]).toBeLessThanOrEqual(intervals[i - 1]);
    }
  });
});

describe("formatSpanLabel", () => {
  it("labels a full day as 24h", () => {
    expect(formatSpanLabel(86_400)).toBe("24h");
  });
  it("labels an hour as 1h", () => {
    expect(formatSpanLabel(3_600)).toBe("1h");
  });
  it("labels 30 minutes as 30m", () => {
    expect(formatSpanLabel(1_800)).toBe("30m");
  });
  it("labels a minute as 1m", () => {
    expect(formatSpanLabel(60)).toBe("1m");
  });
});

describe("formatDurationLabel", () => {
  it("formats the export cap (600s) as 10:00", () => {
    expect(formatDurationLabel(600)).toBe("10:00");
  });
  it("zero-pads seconds", () => {
    expect(formatDurationLabel(65)).toBe("1:05");
  });
});

// ── Time-under-pointer mapping (view-relative, reused percent helpers) ─────────
//
// When zoomed, the inner track still spans the whole day; a pointer x within the
// visible sub-window maps back to an epoch via percentToEpoch over the full day.

describe("time-under-pointer mapping", () => {
  it("maps the track-fraction of a pointer back to a footage epoch", () => {
    // Pointer at 25% of the FULL track = 06:00:00 on a UTC+0 day.
    expect(percentToEpoch(25, DAY_START, DAY_END)).toBe(DAY_START + 21_600);
  });

  it("round-trips an arbitrary in-day epoch through percent", () => {
    const epoch = DAY_START + 5_000;
    const pct = epochToPercent(epoch, DAY_START, DAY_END);
    expect(percentToEpoch(pct, DAY_START, DAY_END)).toBeCloseTo(epoch, 5);
  });
});

// ── Selection clamp via clampClipRange (the affordance Timeline emits) ─────────

describe("range-selection clamp (via clampClipRange)", () => {
  const NOW = DAY_START + 80_000;

  it("orders a backwards drag and returns a valid range", () => {
    const r = clampClipRange(DAY_START + 400, DAY_START + 100, NOW);
    expect(r).not.toBeNull();
    expect(r!.start).toBe(DAY_START + 100);
    expect(r!.end).toBe(DAY_START + 400);
    expect(r!.duration).toBe(300);
  });

  it("caps a selection longer than 10 minutes at the cap", () => {
    const r = clampClipRange(DAY_START, DAY_START + 5_000, NOW);
    expect(r!.duration).toBe(MAX_CLIP_SECONDS); // 600
    expect(r!.end).toBe(DAY_START + MAX_CLIP_SECONDS);
  });

  it("clamps a selection painted into the future down to now", () => {
    const r = clampClipRange(NOW - 100, NOW + 10_000, NOW);
    expect(r!.end).toBe(NOW);
    expect(r!.start).toBe(NOW - 100);
  });

  it("returns null for a zero-length selection (should clear)", () => {
    expect(clampClipRange(DAY_START + 500, DAY_START + 500, NOW)).toBeNull();
  });
});
