import { describe, it, expect } from "vitest";
import {
  footageEpoch,
  findClipAt,
  snapToNearest,
  dayToEpochs,
  epochToNvrTimeStr,
  formatNvrDatetime,
  buildSnapshotFilename,
  httpToWsBase,
  buildPlaybackWsUrl,
} from "./playback-utils";
import { CONFIG } from "@/lib/config";
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

// ── buildSnapshotFilename ──────────────────────────────────────────────────────

describe("buildSnapshotFilename", () => {
  // epoch 1751241600 = 2025-06-30T00:00:00Z (verified: existing dayToEpochs test)

  it("builds a filename with correct NVR-local datetime for UTC+0", () => {
    expect(buildSnapshotFilename(1751241600, 0, "cam1")).toBe(
      "snapshot_cam1_2025-06-30_00-00-00.png",
    );
  });

  it("applies positive tz offset (UTC+5 = 300 min)", () => {
    // 2025-06-30T00:00:00Z + 5h = 2025-06-30 05:00:00 NVR-local
    expect(buildSnapshotFilename(1751241600, 300, "cam1")).toBe(
      "snapshot_cam1_2025-06-30_05-00-00.png",
    );
  });

  it("applies negative tz offset (UTC-6 = -360 min) — date rolls back", () => {
    // 2025-06-30T00:00:00Z − 6h = 2025-06-29 18:00:00 NVR-local
    expect(buildSnapshotFilename(1751241600, -360, "cam1")).toBe(
      "snapshot_cam1_2025-06-29_18-00-00.png",
    );
  });

  it("sanitizes camName: spaces and slashes → underscores", () => {
    expect(buildSnapshotFilename(1751241600, 0, "Front Gate")).toBe(
      "snapshot_Front_Gate_2025-06-30_00-00-00.png",
    );
  });

  it("sanitizes camName: special characters → underscores", () => {
    // "Hall/cam #2" → "Hall_cam__2"
    expect(buildSnapshotFilename(1751241600, 0, "Hall/cam #2")).toBe(
      "snapshot_Hall_cam__2_2025-06-30_00-00-00.png",
    );
  });

  it("filename uses underscore between date and time (filesystem-safe)", () => {
    const filename = buildSnapshotFilename(1751241600, 0, "c");
    // Must not have colons or bare spaces in the datetime portion
    expect(filename).not.toMatch(/[:]/);
    expect(filename).toBe("snapshot_c_2025-06-30_00-00-00.png");
  });
});

// ── httpToWsBase ───────────────────────────────────────────────────────────────

describe("httpToWsBase", () => {
  it("rewrites https:// → wss:// (Caddy TLS origin)", () => {
    expect(httpToWsBase("https://10.10.1.152:8443/api/v1")).toBe(
      "wss://10.10.1.152:8443/api/v1",
    );
  });

  it("rewrites http:// → ws:// (dev origin)", () => {
    expect(httpToWsBase("http://10.10.1.152:8000/api/v1")).toBe(
      "ws://10.10.1.152:8000/api/v1",
    );
  });

  it("only touches the leading scheme, not later 'http' substrings", () => {
    expect(httpToWsBase("https://h/api?u=http://x")).toBe("wss://h/api?u=http://x");
  });
});

// ── buildPlaybackWsUrl ───────────────────────────────────────────────────────────

describe("buildPlaybackWsUrl", () => {
  it("targets the /playback/{nvr}/{channel}/stream WS path", () => {
    const url = buildPlaybackWsUrl("nvr-1", 3, "tok", 1_719_734_400);
    expect(url).toBe(
      `${httpToWsBase(CONFIG.backendBase)}/playback/nvr-1/3/stream?token=tok&t=1719734400&transport=udp`,
    );
  });

  it("derives a ws(s):// URL from the backend origin", () => {
    const url = buildPlaybackWsUrl("nvr-1", 1, "tok", 1_719_734_400);
    expect(url.startsWith("ws://") || url.startsWith("wss://")).toBe(true);
  });

  it("percent-encodes the token (browsers can't set WS auth headers)", () => {
    const url = buildPlaybackWsUrl("nvr-1", 1, "a b/c+&=*", 1_719_734_400);
    expect(url).toContain("token=a%20b%2Fc%2B%26%3D*");
    expect(url).not.toContain("a b/c");
  });

  it("includes t=<initialSeek> so the backend can start at the right epoch (Contract #2)", () => {
    // Backend requires ?t=<epoch> and closes 4004 if it is missing.
    const epoch = 1_751_241_600;
    const url = buildPlaybackWsUrl("nvr-1", 2, "tok", epoch);
    expect(url).toContain(`t=${epoch}`);
  });

  it("places t= after token= in the query string", () => {
    const url = buildPlaybackWsUrl("nvr-1", 1, "tok", 9_999_999);
    expect(url).toMatch(/token=.*&t=9999999&transport=udp$/);
  });

  it("defaults transport to udp when omitted", () => {
    const url = buildPlaybackWsUrl("nvr-1", 1, "tok", 1_719_734_400);
    expect(url).toContain("&transport=udp");
  });

  it("appends transport=tcp when the Clear (TCP) transport is selected", () => {
    const url = buildPlaybackWsUrl("nvr-1", 1, "tok", 1_719_734_400, "tcp");
    expect(url).toContain("&transport=tcp");
    expect(url).not.toContain("transport=udp");
  });

  it("appends transport=udp when explicitly passed", () => {
    const url = buildPlaybackWsUrl("nvr-1", 1, "tok", 1_719_734_400, "udp");
    expect(url).toContain("&transport=udp");
  });
});
