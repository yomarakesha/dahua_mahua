import { describe, it, expect } from "vitest";
import {
  footageEpoch,
  findClipAt,
  snapToNearest,
  dayToEpochs,
  epochToNvrTimeStr,
  formatNvrDatetime,
} from "./playback-utils";
import type { RecordingClip } from "@/api/types";

// ── footageEpoch ──────────────────────────────────────────────────────────────

describe("footageEpoch", () => {
  it("advances from t0 when baseCt=0", () => {
    expect(footageEpoch({ t0: 1000, baseCt: 0, speed: 1 }, 5)).toBe(1005);
  });

  it("subtracts baseCt before adding to t0", () => {
    // currentTime=5, baseCt=3 → delta=2 → footage = 1000+2 = 1002
    expect(footageEpoch({ t0: 1000, baseCt: 3, speed: 1 }, 5)).toBe(1002);
  });

  it("uses addition (not subtraction) of currentTime relative to baseCt", () => {
    // If baseCt were added instead of subtracted, result would be 1000+(5+3)=1008, not 1002
    const result = footageEpoch({ t0: 1000, baseCt: 3, speed: 1 }, 5);
    expect(result).not.toBe(1008);
    expect(result).toBe(1002);
  });

  it("returns t0 exactly when currentTime equals baseCt", () => {
    expect(footageEpoch({ t0: 9999, baseCt: 7, speed: 2 }, 7)).toBe(9999);
  });
});

// ── dayToEpochs ───────────────────────────────────────────────────────────────

describe("dayToEpochs", () => {
  // NVR uses a fixed UTC offset — NOT DST-aware. The day is always exactly 86400 s.
  // NVR local = UTC + tz_offset_minutes  →  UTC = NVR local − offset
  // dayStart(NVR-local 00:00) = UTC = YYYY-MM-DDT00:00:00 local − offset

  it("UTC+5 (300 min): 2026-01-01 → day boundaries", () => {
    // NVR midnight 2026-01-01 00:00 local = UTC 2025-12-31T19:00:00Z
    const expected_start = Date.UTC(2025, 11, 31, 19, 0, 0) / 1000; // 2025-12-31T19:00:00Z
    const expected_end   = expected_start + 86400;
    const [start, end] = dayToEpochs("2026-01-01", 300);
    expect(start).toBe(expected_start);
    expect(end).toBe(expected_end);
  });

  it("UTC+0 (0 min): 2025-06-30 → [1751241600, 1751328000]", () => {
    // 2025-06-30T00:00:00Z = 1751241600  (brief had a typo: "2026-06-30" but those
    // epoch values correspond to 2025-06-30; verified: 20089+180 days × 86400s)
    const [start, end] = dayToEpochs("2025-06-30", 0);
    expect(start).toBe(1751241600);
    expect(end).toBe(1751328000);
  });

  it("UTC-6 (-360 min): 2026-03-08 → dayStart = 2026-03-08T06:00:00Z", () => {
    // NVR midnight local = UTC 2026-03-08T06:00:00Z (NVR has fixed offset, no DST)
    // The NVR's day is always exactly 86400 s regardless of browser's DST transition.
    const expected_start = Date.UTC(2026, 2, 8, 6, 0, 0) / 1000;
    const [start, end] = dayToEpochs("2026-03-08", -360);
    expect(start).toBe(expected_start);
    expect(end).toBe(expected_start + 86400);
  });

  it("always returns exactly 86400 s span (NVR uses fixed offset, no DST)", () => {
    const [start, end] = dayToEpochs("2026-03-08", -360);
    expect(end - start).toBe(86400);

    const [s2, e2] = dayToEpochs("2026-11-01", -300);
    expect(e2 - s2).toBe(86400);
  });
});

// ── findClipAt ────────────────────────────────────────────────────────────────

const clips: RecordingClip[] = [
  { start_epoch: 100, end_epoch: 200, type: "dav", stream: "Main" },
  { start_epoch: 300, end_epoch: 400, type: "dav", stream: "Main" },
];

describe("findClipAt", () => {
  it("returns the clip when epoch is within it", () => {
    expect(findClipAt(clips, 150)).toEqual(clips[0]);
  });

  it("returns the clip at exact start epoch (inclusive)", () => {
    expect(findClipAt(clips, 100)).toEqual(clips[0]);
    expect(findClipAt(clips, 300)).toEqual(clips[1]);
  });

  it("returns null at exact end epoch (exclusive boundary)", () => {
    expect(findClipAt(clips, 200)).toBeNull();
  });

  it("returns null when epoch is in a gap between clips", () => {
    expect(findClipAt(clips, 250)).toBeNull();
  });

  it("returns null when epoch is before all clips", () => {
    expect(findClipAt(clips, 50)).toBeNull();
  });

  it("returns null when epoch is after all clips", () => {
    expect(findClipAt(clips, 500)).toBeNull();
  });

  it("handles empty clips array", () => {
    expect(findClipAt([], 100)).toBeNull();
  });
});

// ── snapToNearest ─────────────────────────────────────────────────────────────

describe("snapToNearest", () => {
  it("returns epoch unchanged when inside a clip", () => {
    expect(snapToNearest(clips, 150)).toBe(150);
  });

  it("returns epoch unchanged at exact start of clip", () => {
    expect(snapToNearest(clips, 100)).toBe(100);
  });

  it("snaps forward to next clip start when in a gap", () => {
    // gap [200, 300) → snap to start of second clip = 300
    expect(snapToNearest(clips, 250)).toBe(300);
  });

  it("snaps to first clip start when before all clips", () => {
    expect(snapToNearest(clips, 50)).toBe(100);
  });

  it("returns null when epoch is after last clip (eof / no_coverage)", () => {
    expect(snapToNearest(clips, 500)).toBeNull();
  });

  it("returns null for empty clips array", () => {
    expect(snapToNearest([], 100)).toBeNull();
  });

  it("returns null at exact end of last clip", () => {
    // end_epoch is exclusive, so 400 is past the last clip
    expect(snapToNearest(clips, 400)).toBeNull();
  });
});

// ── epochToNvrTimeStr ─────────────────────────────────────────────────────────

describe("epochToNvrTimeStr", () => {
  it("UTC+5: formats 12:30:00 NVR-local correctly", () => {
    // 12:30:00 NVR-local (UTC+5) = 07:30:00 UTC
    const epoch = Date.UTC(2026, 0, 1, 7, 30, 0) / 1000;
    expect(epochToNvrTimeStr(epoch, 300)).toBe("12:30:00");
  });

  it("UTC-3: formats midnight NVR-local correctly", () => {
    // 00:00:00 NVR-local (UTC-3) = 03:00:00 UTC
    const epoch = Date.UTC(2026, 0, 1, 3, 0, 0) / 1000;
    expect(epochToNvrTimeStr(epoch, -180)).toBe("00:00:00");
  });

  it("pads hours, minutes, seconds to two digits", () => {
    // 01:02:03 NVR-local (UTC+0) = 01:02:03 UTC
    const epoch = Date.UTC(2026, 0, 1, 1, 2, 3) / 1000;
    expect(epochToNvrTimeStr(epoch, 0)).toBe("01:02:03");
  });

  it("UTC+0: just uses UTC time directly", () => {
    const epoch = Date.UTC(2026, 5, 30, 14, 45, 59) / 1000;
    expect(epochToNvrTimeStr(epoch, 0)).toBe("14:45:59");
  });
});

// ── formatNvrDatetime ─────────────────────────────────────────────────────────

describe("formatNvrDatetime", () => {
  it("formats full datetime in NVR-local timezone (UTC+5)", () => {
    // 2026-01-15 08:30:00 NVR-local (UTC+5) = 2026-01-15T03:30:00Z
    const epoch = Date.UTC(2026, 0, 15, 3, 30, 0) / 1000;
    expect(formatNvrDatetime(epoch, 300)).toBe("2026-01-15 08:30:00");
  });

  it("formats midnight rollover correctly (UTC-3)", () => {
    // 2026-03-01 00:00:00 NVR-local (UTC-3) = 2026-03-01T03:00:00Z
    const epoch = Date.UTC(2026, 2, 1, 3, 0, 0) / 1000;
    expect(formatNvrDatetime(epoch, -180)).toBe("2026-03-01 00:00:00");
  });

  it("pads all components to two digits (UTC+0)", () => {
    // 2026-02-03 04:05:06 UTC+0
    const epoch = Date.UTC(2026, 1, 3, 4, 5, 6) / 1000;
    expect(formatNvrDatetime(epoch, 0)).toBe("2026-02-03 04:05:06");
  });
});
