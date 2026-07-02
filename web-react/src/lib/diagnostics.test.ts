import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  recordEvent,
  shipLogs,
  __resetDiagnosticsForTest,
  __ringSnapshotForTest,
} from "./diagnostics";

describe("diagnostics ring buffer", () => {
  beforeEach(() => {
    __resetDiagnosticsForTest();
  });

  it("caps the ring at 300 entries (oldest dropped)", () => {
    for (let i = 0; i < 350; i++) recordEvent("t", `event-${i}`);
    const ring = __ringSnapshotForTest();
    expect(ring).toHaveLength(300);
    // The first 50 should have rolled off; the ring should start at event-50.
    expect(ring[0].msg).toBe("[t] event-50");
    expect(ring[ring.length - 1].msg).toBe("[t] event-349");
  });

  it("caps each message at 500 chars", () => {
    recordEvent("t", "x".repeat(1000));
    const ring = __ringSnapshotForTest();
    expect(ring).toHaveLength(1);
    expect(ring[0].msg.length).toBe(500);
  });
});

describe("diagnostics shipLogs throttle", () => {
  let beacon: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.useFakeTimers();
    __resetDiagnosticsForTest();
    beacon = vi.fn(() => true);
    Object.defineProperty(navigator, "sendBeacon", { value: beacon, configurable: true });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("ships at most once per 30 s, dropping (counting) the rest", () => {
    expect(shipLogs("first")).toBe(true);
    expect(shipLogs("second")).toBe(false); // within 30 s → throttled
    expect(shipLogs("third")).toBe(false);
    expect(beacon).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(30_001);
    expect(shipLogs("later")).toBe(true);
    expect(beacon).toHaveBeenCalledTimes(2);
  });

  it("POSTs to the /client-log endpoint", () => {
    shipLogs("reason");
    expect(beacon).toHaveBeenCalledTimes(1);
    const endpoint = beacon.mock.calls[0][0] as string;
    expect(endpoint).toContain("/client-log");
  });

  it("crash-class reasons bypass the throttle (last chance to ship)", () => {
    expect(shipLogs("routine")).toBe(true);
    expect(shipLogs("routine")).toBe(false); // throttled
    expect(shipLogs("react-crash")).toBe(true); // crash always ships
    expect(shipLogs("uncaught")).toBe(true);
    expect(beacon).toHaveBeenCalledTimes(3);
  });

  it("falls back to fetch(keepalive) when sendBeacon rejects the payload", async () => {
    beacon.mockReturnValue(false); // e.g. over the ~64KB beacon budget
    const fetchSpy = vi.fn(() => Promise.resolve(new Response()));
    vi.stubGlobal("fetch", fetchSpy);
    try {
      expect(shipLogs("big")).toBe(true);
      expect(beacon).toHaveBeenCalledTimes(1);
      expect(fetchSpy).toHaveBeenCalledTimes(1);
      const [, init] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
      expect(init.keepalive).toBe(true);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("shipped body matches the backend ClientLogEntry schema", () => {
    recordEvent("test", "hello schema");
    shipLogs("shape");
    const blob = beacon.mock.calls[0][1] as Blob;
    // jsdom Blob supports .text() — but keep it sync-safe via the constructor parts
    return blob.text().then((text) => {
      const payload = JSON.parse(text) as { entries: Record<string, unknown>[] };
      expect(Array.isArray(payload.entries)).toBe(true);
      expect(payload.entries.length).toBeGreaterThan(0);
      for (const e of payload.entries) {
        // exactly the backend model's fields — nothing extra, nothing missing
        expect(Object.keys(e).sort()).toEqual(["detail", "level", "msg", "path", "ts"]);
        expect(["DEBUG", "INFO", "WARNING", "ERROR"]).toContain(e.level);
        expect(typeof e.msg).toBe("string");
        expect((e.msg as string).length).toBeLessThanOrEqual(2000);
      }
    });
  });
});
