/**
 * Timeline component render / keyboard / interaction tests.
 *
 * Pure math helper tests live in Timeline.test.ts.
 *
 * NOTE on pointer-drag: jsdom does not implement getBoundingClientRect() or
 * setPointerCapture(), so full drag-interaction tests (pointerdown → pointermove
 * → pointerup) are DEFERRED to manual / Playwright e2e testing.
 * What IS tested here:
 *   - role="slider" present with ARIA attributes
 *   - Keyboard ArrowRight / ArrowLeft calls onSeek
 *   - Home / End keys seek to first / last clip
 *   - PageDown / PageUp call onSeek for next / prev clip
 *   - Prev / next clip buttons exist and are clickable
 *   - Buttons disabled when playerState === "error"
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import Timeline from "./Timeline";
import type { RecordingClip } from "@/api/types";
import type { PlayerState } from "./types";

// ── Fixtures ──────────────────────────────────────────────────────────────────

/** 2025-06-30T00:00:00Z */
const DAY_START = 1_751_241_600;
const DAY_END   = DAY_START + 86_400;

const CLIPS: RecordingClip[] = [
  // clip 1: 01:00–02:00
  { start_epoch: DAY_START + 3_600,  end_epoch: DAY_START + 7_200,  type: "dav", stream: "Main" },
  // clip 2: 04:00–05:00
  { start_epoch: DAY_START + 14_400, end_epoch: DAY_START + 18_000, type: "dav", stream: "Main" },
];

// ── Helpers ───────────────────────────────────────────────────────────────────

function renderTimeline(opts: {
  playerState?: PlayerState;
  playheadEpoch?: number | null;
  onSeek?: (e: number) => void;
  clips?: RecordingClip[];
  onPrevClip?: () => void;
  onNextClip?: () => void;
} = {}) {
  const onSeek = opts.onSeek ?? vi.fn();
  render(
    <Timeline
      dayStartEpoch={DAY_START}
      dayEndEpoch={DAY_END}
      clips={opts.clips ?? CLIPS}
      tzOffsetMinutes={0}
      playheadEpoch={opts.playheadEpoch !== undefined ? opts.playheadEpoch : DAY_START + 5_000}
      onSeek={onSeek}
      playerState={opts.playerState ?? "playing"}
      onPrevClip={opts.onPrevClip}
      onNextClip={opts.onNextClip}
    />,
  );
  return { onSeek };
}

// ── ARIA / role ───────────────────────────────────────────────────────────────

describe("Timeline ARIA", () => {
  it("renders an element with role='slider'", () => {
    renderTimeline();
    expect(screen.getByRole("slider")).toBeTruthy();
  });

  it("slider has aria-valuemin = dayStartEpoch", () => {
    renderTimeline();
    const slider = screen.getByRole("slider");
    expect(Number(slider.getAttribute("aria-valuemin"))).toBe(DAY_START);
  });

  it("slider has aria-valuemax = dayEndEpoch", () => {
    renderTimeline();
    const slider = screen.getByRole("slider");
    expect(Number(slider.getAttribute("aria-valuemax"))).toBe(DAY_END);
  });

  it("slider aria-valuenow reflects playheadEpoch", () => {
    const epoch = DAY_START + 5_000;
    renderTimeline({ playheadEpoch: epoch });
    const slider = screen.getByRole("slider");
    expect(Number(slider.getAttribute("aria-valuenow"))).toBe(epoch);
  });

  it("slider aria-valuetext is 'no position' when playheadEpoch is null", () => {
    renderTimeline({ playheadEpoch: null });
    const slider = screen.getByRole("slider");
    expect(slider.getAttribute("aria-valuetext")).toBe("no position");
  });

  it("slider aria-valuetext is NVR-local time when playhead is set", () => {
    // playheadEpoch = DAY_START + 3600 → 01:00:00 at UTC+0
    renderTimeline({ playheadEpoch: DAY_START + 3_600 });
    const slider = screen.getByRole("slider");
    expect(slider.getAttribute("aria-valuetext")).toBe("01:00:00");
  });

  it("slider is tabIndex=0 (keyboard-focusable)", () => {
    renderTimeline();
    const slider = screen.getByRole("slider") as HTMLElement;
    expect(slider.tabIndex).toBe(0);
  });
});

// ── Keyboard navigation ───────────────────────────────────────────────────────

describe("Timeline keyboard navigation", () => {
  it("ArrowRight calls onSeek(playhead + 10)", () => {
    const onSeek = vi.fn();
    const head = DAY_START + 5_000;
    renderTimeline({ onSeek, playheadEpoch: head });
    const slider = screen.getByRole("slider");
    fireEvent.keyDown(slider, { key: "ArrowRight" });
    // snapToNearest(clips, head+10) — head+5010 is inside clip1 [+3600..+7200]
    expect(onSeek).toHaveBeenCalledWith(head + 10);
  });

  it("ArrowLeft calls onSeek(playhead - 10)", () => {
    const onSeek = vi.fn();
    const head = DAY_START + 5_000;
    renderTimeline({ onSeek, playheadEpoch: head });
    const slider = screen.getByRole("slider");
    fireEvent.keyDown(slider, { key: "ArrowLeft" });
    // head - 10 = +4990, still inside clip1 → snapped = +4990
    expect(onSeek).toHaveBeenCalledWith(head - 10);
  });

  it("Home seeks to the first clip start", () => {
    const onSeek = vi.fn();
    renderTimeline({ onSeek, playheadEpoch: DAY_START + 15_000 });
    fireEvent.keyDown(screen.getByRole("slider"), { key: "Home" });
    expect(onSeek).toHaveBeenCalledWith(CLIPS[0].start_epoch);
  });

  it("End seeks to the last clip start", () => {
    const onSeek = vi.fn();
    renderTimeline({ onSeek, playheadEpoch: DAY_START + 5_000 });
    fireEvent.keyDown(screen.getByRole("slider"), { key: "End" });
    expect(onSeek).toHaveBeenCalledWith(CLIPS[CLIPS.length - 1].start_epoch);
  });

  it("PageDown (next clip) seeks to clip2 start when playhead is in clip1", () => {
    const onSeek = vi.fn();
    // playhead inside clip1: DAY_START + 5000
    renderTimeline({ onSeek, playheadEpoch: DAY_START + 5_000 });
    fireEvent.keyDown(screen.getByRole("slider"), { key: "PageDown" });
    expect(onSeek).toHaveBeenCalledWith(CLIPS[1].start_epoch);
  });

  it("PageUp (prev clip) seeks to clip1 start when playhead is in clip2", () => {
    const onSeek = vi.fn();
    // playhead inside clip2: DAY_START + 16000
    renderTimeline({ onSeek, playheadEpoch: DAY_START + 16_000 });
    fireEvent.keyDown(screen.getByRole("slider"), { key: "PageUp" });
    // prev = last clip with end_epoch < 16000+DAY_START
    // clip1.end_epoch = DAY_START+7200 < DAY_START+16000 → prevClip = clip1
    expect(onSeek).toHaveBeenCalledWith(CLIPS[0].start_epoch);
  });

  it("keyboard does nothing when playerState === 'error'", () => {
    const onSeek = vi.fn();
    renderTimeline({ onSeek, playerState: "error" });
    fireEvent.keyDown(screen.getByRole("slider"), { key: "ArrowRight" });
    expect(onSeek).not.toHaveBeenCalled();
  });

  it("ArrowRight in a gap snaps forward to next clip", () => {
    const onSeek = vi.fn();
    // playhead in gap between clips: DAY_START + 10000 (between 7200 and 14400)
    renderTimeline({ onSeek, playheadEpoch: DAY_START + 10_000 });
    fireEvent.keyDown(screen.getByRole("slider"), { key: "ArrowRight" });
    // head + 10 = +10010, still in gap → snapToNearest snaps to clip2.start = +14400
    expect(onSeek).toHaveBeenCalledWith(CLIPS[1].start_epoch);
  });
});

// ── Clamp committed seeks to "now" (HIGH-3) ───────────────────────────────────

describe("Timeline clamps seeks to now (HIGH-3)", () => {
  it("a keyboard seek never commits an epoch past the present", () => {
    const now = 2_000_000_000; // fixed "now" (epoch seconds)
    vi.spyOn(Date, "now").mockReturnValue(now * 1000);
    const onSeek = vi.fn();
    // A "today"-style day whose bar extends into the future; a clip that runs
    // right up to (and slightly past) now so snapToNearest returns a future epoch.
    const dayStart = now - 40_000;
    const dayEnd = dayStart + 86_400; // future tail
    render(
      <Timeline
        dayStartEpoch={dayStart}
        dayEndEpoch={dayEnd}
        clips={[{ start_epoch: now - 100, end_epoch: now + 3_600, type: "Timing", stream: "Main" }]}
        tzOffsetMinutes={0}
        playheadEpoch={now + 50} // ahead of now (inside the clip)
        onSeek={onSeek}
        playerState="playing"
      />,
    );
    // ArrowRight → head+10 = now+60 (inside the clip, so unsnapped) → clamped to now.
    fireEvent.keyDown(screen.getByRole("slider"), { key: "ArrowRight" });
    expect(onSeek).toHaveBeenCalledWith(now);
    vi.restoreAllMocks();
  });

  it("does not clamp a seek that is already in the past", () => {
    const now = 2_000_000_000;
    vi.spyOn(Date, "now").mockReturnValue(now * 1000);
    const onSeek = vi.fn();
    const dayStart = now - 40_000;
    const dayEnd = dayStart + 86_400;
    render(
      <Timeline
        dayStartEpoch={dayStart}
        dayEndEpoch={dayEnd}
        clips={[{ start_epoch: now - 20_000, end_epoch: now - 10_000, type: "Timing", stream: "Main" }]}
        tzOffsetMinutes={0}
        playheadEpoch={now - 15_000}
        onSeek={onSeek}
        playerState="playing"
      />,
    );
    fireEvent.keyDown(screen.getByRole("slider"), { key: "ArrowRight" });
    // head+10 = now-14990, still in the past → passed through unchanged.
    expect(onSeek).toHaveBeenCalledWith(now - 14_990);
    vi.restoreAllMocks();
  });
});

// ── Prev / next clip buttons ──────────────────────────────────────────────────

describe("Timeline prev/next buttons", () => {
  it("renders a 'Previous clip' button", () => {
    renderTimeline();
    expect(screen.getByRole("button", { name: /previous clip/i })).toBeTruthy();
  });

  it("renders a 'Next clip' button", () => {
    renderTimeline();
    expect(screen.getByRole("button", { name: /next clip/i })).toBeTruthy();
  });

  it("Next clip button calls onSeek with clip2 start when playhead is in clip1", () => {
    const onSeek = vi.fn();
    renderTimeline({ onSeek, playheadEpoch: DAY_START + 5_000 });
    fireEvent.click(screen.getByRole("button", { name: /next clip/i }));
    expect(onSeek).toHaveBeenCalledWith(CLIPS[1].start_epoch);
  });

  it("Prev clip button calls onSeek with clip1 start when playhead is in clip2", () => {
    const onSeek = vi.fn();
    renderTimeline({ onSeek, playheadEpoch: DAY_START + 16_000 });
    fireEvent.click(screen.getByRole("button", { name: /previous clip/i }));
    expect(onSeek).toHaveBeenCalledWith(CLIPS[0].start_epoch);
  });

  it("buttons are disabled when playerState === 'error'", () => {
    renderTimeline({ playerState: "error" });
    expect(
      (screen.getByRole("button", { name: /previous clip/i }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(
      (screen.getByRole("button", { name: /next clip/i }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it("optional onPrevClip callback fires when prev button is clicked", () => {
    const onPrevClip = vi.fn();
    const onSeek = vi.fn();
    // playhead in clip2 so there IS a prev clip
    renderTimeline({ onSeek, onPrevClip, playheadEpoch: DAY_START + 16_000 });
    fireEvent.click(screen.getByRole("button", { name: /previous clip/i }));
    expect(onPrevClip).toHaveBeenCalledOnce();
  });

  it("optional onNextClip callback fires when next button is clicked", () => {
    const onNextClip = vi.fn();
    const onSeek = vi.fn();
    // playhead in clip1 so there IS a next clip
    renderTimeline({ onSeek, onNextClip, playheadEpoch: DAY_START + 5_000 });
    fireEvent.click(screen.getByRole("button", { name: /next clip/i }));
    expect(onNextClip).toHaveBeenCalledOnce();
  });
});

// ── No-clip edge cases ────────────────────────────────────────────────────────

describe("Timeline with no clips", () => {
  it("still renders the slider element", () => {
    renderTimeline({ clips: [] });
    expect(screen.getByRole("slider")).toBeTruthy();
  });

  it("keyboard does nothing when clips is empty", () => {
    const onSeek = vi.fn();
    renderTimeline({ onSeek, clips: [] });
    fireEvent.keyDown(screen.getByRole("slider"), { key: "ArrowRight" });
    expect(onSeek).not.toHaveBeenCalled();
  });
});

// ── Drag interaction — DEFERRED ───────────────────────────────────────────────
//
// Full pointer-drag (pointerdown → pointermove → pointerup with commit-on-release)
// cannot be tested in jsdom because:
//   1. jsdom does not implement getBoundingClientRect() (returns all-zero rect)
//   2. jsdom does not implement setPointerCapture()
//
// DEFERRED to manual / Playwright e2e testing:
//   - Ghost playhead follows pointer during drag
//   - onSeek fires EXACTLY ONCE on pointerup (not on pointermove)
//   - Releasing in a gap snaps to the next clip start
//   - Releasing past all clips is a no-op (does not seek into the void)
