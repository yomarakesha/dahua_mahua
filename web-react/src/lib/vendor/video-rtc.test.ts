/**
 * Batch-2 #3 — vendor reconnect jitter + floor. The base reconnect delay is
 * max(RECONNECT_TIMEOUT - elapsed, 0), which is 0 for any long-connected tile →
 * on a go2rtc restart every such tile stampedes at delay=0. The DSS patch adds
 * random jitter and a small floor to spread the herd. We assert the scheduled
 * setTimeout delay honours both. Also a smoke import of the vendor module.
 */
import { describe, it, expect, vi, beforeAll, afterEach } from "vitest";
import { VideoRTC } from "./video-rtc.js";
import { registerDssMse } from "@/components/video/dss-mse";

// jsdom has no WebSocket; the VideoRTC constructor reads WebSocket.CLOSED. Provide
// a minimal stub with the state constants (no instances are created by onclose()).
beforeAll(() => {
  if (typeof (globalThis as { WebSocket?: unknown }).WebSocket === "undefined") {
    class WS {
      static CONNECTING = 0;
      static OPEN = 1;
      static CLOSING = 2;
      static CLOSED = 3;
    }
    (globalThis as unknown as { WebSocket: unknown }).WebSocket = WS;
  }
  registerDssMse();
});

afterEach(() => vi.restoreAllMocks());

function makeEl(): VideoRTC {
  const el = document.createElement("dss-mse") as unknown as VideoRTC & {
    wsState: number;
    connectTS: number;
    onclose: () => boolean;
  };
  // Simulate a tile that has been connected far longer than RECONNECT_TIMEOUT so
  // the base term is 0 — the exact "stampede at delay 0" case.
  el.wsState = (globalThis as unknown as { WebSocket: { OPEN: number } }).WebSocket.OPEN;
  el.connectTS = 0; // epoch → elapsed is huge → base delay 0
  return el as unknown as VideoRTC;
}

describe("VideoRTC smoke", () => {
  it("exports the class", () => {
    expect(typeof VideoRTC).toBe("function");
  });
});

describe("VideoRTC.onclose reconnect delay (#3 jitter + floor)", () => {
  it("never schedules below the 500ms floor even when base is 0", () => {
    const el = makeEl() as unknown as { onclose: () => void };
    vi.spyOn(Math, "random").mockReturnValue(0); // no jitter → floor must apply
    const spy = vi.spyOn(globalThis, "setTimeout");
    el.onclose();
    const c = spy.mock.calls;
    const delay = c[c.length - 1]?.[1] as number;
    expect(delay).toBe(500);
  });

  it("adds random jitter (spreads the herd) above the floor", () => {
    const el = makeEl() as unknown as { onclose: () => void };
    vi.spyOn(Math, "random").mockReturnValue(1); // max jitter 3000
    const spy = vi.spyOn(globalThis, "setTimeout");
    el.onclose();
    const c = spy.mock.calls;
    const delay = c[c.length - 1]?.[1] as number;
    expect(delay).toBe(3000);
  });

  it("distinct tiles get distinct delays (no lockstep)", () => {
    const randoms = [0.1, 0.7];
    let i = 0;
    vi.spyOn(Math, "random").mockImplementation(() => randoms[i++ % randoms.length]);
    const spy = vi.spyOn(globalThis, "setTimeout");
    (makeEl() as unknown as { onclose: () => void }).onclose();
    (makeEl() as unknown as { onclose: () => void }).onclose();
    const c = spy.mock.calls;
    const d1 = c[c.length - 2]?.[1] as number;
    const d2 = c[c.length - 1]?.[1] as number;
    expect(d1).not.toBe(d2);
  });
});
