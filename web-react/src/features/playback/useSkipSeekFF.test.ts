import { renderHook } from "@testing-library/react";
import { vi, describe, it, expect, beforeEach, afterEach } from "vitest";
import { useSkipSeekFF } from "./useSkipSeekFF";

const STEP_MS = 2500;

describe("useSkipSeekFF", () => {
  let nowMs = 0;
  beforeEach(() => {
    vi.useFakeTimers();
    nowMs = 0;
    vi.spyOn(performance, "now").mockImplementation(() => nowMs);
    // Far-future system time so Date.now()/1000 never caps in the Nx tests.
    vi.setSystemTime(new Date("2026-07-07T12:00:00Z"));
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });
  function advance(ms: number) {
    nowMs += ms;
    vi.advanceTimersByTime(ms);
  }

  const FAR_END = 10_000_000_000; // year ~2286, never the cap

  it("steps the seek forward at Nx of wall-clock time", () => {
    const onSeek = vi.fn();
    renderHook(() =>
      useSkipSeekFF({ speed: 4, playheadEpoch: 1000, dayEndEpoch: FAR_END, onSeek, onReachedEnd: () => {} }),
    );
    advance(STEP_MS); // 2.5s elapsed → 1000 + 4*2.5 = 1010
    expect(onSeek).toHaveBeenLastCalledWith(1010);
    advance(STEP_MS); // 5s elapsed → 1000 + 4*5 = 1020
    expect(onSeek).toHaveBeenLastCalledWith(1020);
  });

  it("does nothing at 1x (normal playback)", () => {
    const onSeek = vi.fn();
    renderHook(() =>
      useSkipSeekFF({ speed: 1, playheadEpoch: 1000, dayEndEpoch: FAR_END, onSeek, onReachedEnd: () => {} }),
    );
    advance(STEP_MS * 3);
    expect(onSeek).not.toHaveBeenCalled();
  });

  it("does not start until the player reports a playhead", () => {
    const onSeek = vi.fn();
    renderHook(() =>
      useSkipSeekFF({ speed: 4, playheadEpoch: null, dayEndEpoch: FAR_END, onSeek, onReachedEnd: () => {} }),
    );
    advance(STEP_MS * 2);
    expect(onSeek).not.toHaveBeenCalled();
  });

  it("stops at the day/live edge and asks the parent to drop to 1x", () => {
    const onSeek = vi.fn();
    const onReachedEnd = vi.fn();
    // dayEnd just ahead of the playhead → the first Nx step overshoots the cap.
    renderHook(() =>
      useSkipSeekFF({ speed: 8, playheadEpoch: 1000, dayEndEpoch: 1005, onSeek, onReachedEnd }),
    );
    advance(STEP_MS); // target 1000+8*2.5=1020 >= cap(1005) → seek to cap + end
    expect(onSeek).toHaveBeenLastCalledWith(1005);
    expect(onReachedEnd).toHaveBeenCalledTimes(1);
  });
});
