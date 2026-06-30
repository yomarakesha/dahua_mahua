/**
 * Timeline — 24-hour recorded-clip bar with draggable playhead.
 *
 * Features:
 *  - Clip segments shaded in accent color
 *  - Pointer drag → commit-on-release (onSeek fires once on pointerup, NOT on move)
 *  - Ghost playhead + NVR-local time label during drag
 *  - Gap snap via snapToNearest (from playback-utils)
 *  - role="slider" + keyboard: ←/→ = ±10s, Home/End = first/last clip, PageUp/Down = prev/next
 *  - Prev/next clip buttons at bar edges
 *  - Axis: hour labels at 0, 3, 6, 9, 12, 15, 18, 21 in NVR-local time
 *  - Drag disabled when playerState === "error"
 *
 * Thumbnail preview: deferred (backend /thumb endpoint not yet wired).
 * TODO(Phase-3 follow-up): thumbnail endpoint — fetch /thumb?at=<epoch> via the
 *   existing `http` Bearer-authenticated client → objectURL, throttled on
 *   drag-settle.  Can't use a plain <img src> because it can't send auth headers.
 */

import { useRef, useState } from "react";
import type { RecordingClip } from "@/api/types";
import type { PlayerState } from "./types";
import { epochToNvrTimeStr, snapToNearest } from "./playback-utils";

// ── Pure helpers (exported for Vitest) ────────────────────────────────────────

/**
 * Map a UTC epoch to a percentage position [0, 100] within the day.
 *
 * Uses `dayEnd - dayStart` as the divisor so 23h/25h DST days are handled
 * correctly — never hard-codes 86 400.
 */
export function epochToPercent(
  epoch: number,
  dayStart: number,
  dayEnd: number,
): number {
  const dayDuration = dayEnd - dayStart;
  if (dayDuration <= 0) return 0;
  return ((epoch - dayStart) / dayDuration) * 100;
}

/**
 * Inverse of epochToPercent — map a percentage [0, 100] back to a UTC epoch.
 */
export function percentToEpoch(
  pct: number,
  dayStart: number,
  dayEnd: number,
): number {
  const dayDuration = dayEnd - dayStart;
  return dayStart + (pct / 100) * dayDuration;
}

// ── Types ─────────────────────────────────────────────────────────────────────

interface TimelineProps {
  /** Day boundaries from RecordingIndex (UTC epoch seconds). */
  dayStartEpoch: number;
  dayEndEpoch: number;

  /** Merged clip spans for the day. */
  clips: RecordingClip[];

  /** NVR timezone offset (minutes, same sign as backend). */
  tzOffsetMinutes: number;

  /** Current playhead position (footage epoch). Drives the playhead marker. */
  playheadEpoch: number | null;

  /**
   * Fired when the user COMMITS a seek (drag release or keyboard).
   * Timeline fires once on pointerup — no internal debounce.
   * The 250 ms debounce lives in PlaybackPage, not here (Contract §7).
   */
  onSeek: (epoch: number) => void;

  /** Player state — drag/seek are disabled when playerState === "error". */
  playerState: PlayerState;

  /**
   * Optional notification callbacks called by Timeline's own prev/next buttons
   * and PageUp/PageDown keyboard handler.  PlaybackPage may pass these to sync
   * toolbar state; they do NOT drive the Timeline — onSeek is the real driver.
   */
  onPrevClip?: () => void;
  onNextClip?: () => void;
}

type DragState =
  | { dragging: false }
  | { dragging: true; ghostEpoch: number };

// ── Constants ─────────────────────────────────────────────────────────────────

const AXIS_HOURS = [0, 3, 6, 9, 12, 15, 18, 21] as const;

// ── Component ─────────────────────────────────────────────────────────────────

export default function Timeline({
  dayStartEpoch,
  dayEndEpoch,
  clips,
  tzOffsetMinutes,
  playheadEpoch,
  onSeek,
  playerState,
  onPrevClip,
  onNextClip,
}: TimelineProps) {
  const barRef = useRef<HTMLDivElement>(null);
  const [dragState, setDragState] = useState<DragState>({ dragging: false });

  const dayDuration = dayEndEpoch - dayStartEpoch;
  const isDisabled = playerState === "error";

  // ── Pointer → epoch conversion ────────────────────────────────────────────

  function clientXToEpoch(clientX: number): number {
    if (!barRef.current) return dayStartEpoch;
    const rect = barRef.current.getBoundingClientRect();
    const x = Math.max(0, Math.min(rect.width, clientX - rect.left));
    const pct = rect.width > 0 ? (x / rect.width) * 100 : 0;
    return percentToEpoch(pct, dayStartEpoch, dayEndEpoch);
  }

  // ── Pointer handlers (commit-on-release) ──────────────────────────────────

  function handlePointerDown(e: React.PointerEvent<HTMLDivElement>) {
    if (isDisabled) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    const ghostEpoch = clientXToEpoch(e.clientX);
    setDragState({ dragging: true, ghostEpoch });
  }

  function handlePointerMove(e: React.PointerEvent<HTMLDivElement>) {
    if (!dragState.dragging || isDisabled) return;
    const ghostEpoch = clientXToEpoch(e.clientX);
    setDragState({ dragging: true, ghostEpoch });
  }

  function handlePointerUp(e: React.PointerEvent<HTMLDivElement>) {
    if (!dragState.dragging || isDisabled) return;

    const rawEpoch = clientXToEpoch(e.clientX);
    const snapped = snapToNearest(clips, rawEpoch);

    if (snapped !== null) {
      // Fires ONCE on release — no debounce here (debounce lives in PlaybackPage)
      onSeek(snapped);
    } else {
      // Ghost fell past all clips — do not seek into the void.
      // No-op: the day's coverage has ended; PlaybackPage / WS will emit eof/gap.
      // (Alternative: snap to last clip start — but that may confuse the user.)
    }

    setDragState({ dragging: false });
  }

  // ── Keyboard navigation (role="slider") ───────────────────────────────────

  function handleKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
    if (isDisabled || clips.length === 0) return;

    const head = playheadEpoch ?? dayStartEpoch;

    switch (e.key) {
      case "ArrowLeft": {
        e.preventDefault();
        const target = Math.max(dayStartEpoch, head - 10);
        const snapped = snapToNearest(clips, target);
        if (snapped !== null) onSeek(snapped);
        break;
      }
      case "ArrowRight": {
        e.preventDefault();
        const target = Math.min(dayEndEpoch - 1, head + 10);
        const snapped = snapToNearest(clips, target);
        if (snapped !== null) onSeek(snapped);
        break;
      }
      case "Home": {
        e.preventDefault();
        onSeek(clips[0].start_epoch);
        break;
      }
      case "End": {
        e.preventDefault();
        // Seek to the start of the last clip (end_epoch is exclusive)
        onSeek(clips[clips.length - 1].start_epoch);
        break;
      }
      case "PageUp": {
        e.preventDefault();
        seekToPrevClip();
        break;
      }
      case "PageDown": {
        e.preventDefault();
        seekToNextClip();
        break;
      }
    }
  }

  // ── Prev / next clip logic ─────────────────────────────────────────────────

  function seekToPrevClip() {
    if (playheadEpoch === null) return;
    // Last clip whose end_epoch < playheadEpoch
    let prevClip: RecordingClip | null = null;
    for (const clip of clips) {
      if (clip.end_epoch < playheadEpoch) prevClip = clip;
    }
    if (prevClip) {
      onSeek(prevClip.start_epoch);
      onPrevClip?.();
    }
  }

  function seekToNextClip() {
    if (playheadEpoch === null) return;
    // First clip whose start_epoch > playheadEpoch
    for (const clip of clips) {
      if (clip.start_epoch > playheadEpoch) {
        onSeek(clip.start_epoch);
        onNextClip?.();
        return;
      }
    }
  }

  // ── Derived render values ─────────────────────────────────────────────────

  const playheadPct =
    playheadEpoch !== null
      ? epochToPercent(playheadEpoch, dayStartEpoch, dayEndEpoch)
      : null;

  const ghostPct =
    dragState.dragging
      ? epochToPercent(dragState.ghostEpoch, dayStartEpoch, dayEndEpoch)
      : null;

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="relative flex flex-col select-none px-6">
      {/* Prev clip button — left edge */}
      <button
        aria-label="Previous clip"
        disabled={isDisabled || playheadEpoch === null || clips.length === 0}
        onClick={seekToPrevClip}
        className="absolute left-0 top-0 z-10 flex h-14 w-6 items-center justify-center text-lg text-ink-dim transition hover:text-ink-soft disabled:cursor-not-allowed disabled:opacity-30"
      >
        ‹
      </button>

      {/* Next clip button — right edge */}
      <button
        aria-label="Next clip"
        disabled={isDisabled || playheadEpoch === null || clips.length === 0}
        onClick={seekToNextClip}
        className="absolute right-0 top-0 z-10 flex h-14 w-6 items-center justify-center text-lg text-ink-dim transition hover:text-ink-soft disabled:cursor-not-allowed disabled:opacity-30"
      >
        ›
      </button>

      {/* ── Main bar (role="slider") ─────────────────────────────────────── */}
      <div
        ref={barRef}
        role="slider"
        aria-valuemin={dayStartEpoch}
        aria-valuemax={dayEndEpoch}
        aria-valuenow={playheadEpoch ?? dayStartEpoch}
        aria-valuetext={
          playheadEpoch !== null
            ? epochToNvrTimeStr(playheadEpoch, tzOffsetMinutes)
            : "no position"
        }
        aria-disabled={isDisabled}
        tabIndex={0}
        className={[
          "relative h-14 overflow-hidden rounded-md bg-[#0c1014]",
          isDisabled ? "cursor-not-allowed opacity-50" : "cursor-crosshair",
          "focus:outline-none focus-visible:ring-1 focus-visible:ring-accent/50",
        ].join(" ")}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onKeyDown={handleKeyDown}
      >
        {/* Clip segments */}
        {clips.map((clip, i) => {
          const left = epochToPercent(clip.start_epoch, dayStartEpoch, dayEndEpoch);
          const right = epochToPercent(clip.end_epoch, dayStartEpoch, dayEndEpoch);
          const width = Math.max(0, right - left);
          return (
            <div
              key={i}
              className="absolute top-0 h-full bg-accent/35 border-x border-accent/20"
              style={{ left: `${left}%`, width: `${width}%` }}
            />
          );
        })}

        {/* Playhead marker — hidden while dragging */}
        {playheadPct !== null && !dragState.dragging && (
          <div
            className="pointer-events-none absolute top-0 h-full w-0.5 -translate-x-px bg-white shadow-[0_0_6px_rgba(255,255,255,0.6)]"
            style={{ left: `${playheadPct}%` }}
          />
        )}

        {/* Ghost playhead + time label during drag */}
        {ghostPct !== null && dragState.dragging && (
          <>
            {/* Ghost line */}
            <div
              className="pointer-events-none absolute top-0 h-full w-0.5 -translate-x-px bg-white/40"
              style={{ left: `${ghostPct}%` }}
            />
            {/* NVR-local time label */}
            <div
              className="pointer-events-none absolute top-1.5 -translate-x-1/2 whitespace-nowrap rounded bg-black/80 px-1.5 py-0.5 font-mono text-[10px] text-white/90"
              style={{ left: `${ghostPct}%` }}
            >
              {epochToNvrTimeStr(dragState.ghostEpoch, tzOffsetMinutes)}
            </div>
            {/*
              TODO(Phase-3 follow-up): thumbnail endpoint image preview.
              Plan: fetch /playback/{nvrId}/{ch}/thumb?at=<epoch> via the
              existing Bearer-authenticated http client (plain <img src> cannot
              send auth headers) → URL.createObjectURL(), revoke previous blob.
              Throttle to drag-settle / pointerup, not every pointermove.
              Block on Phase-2 Task 9 /thumb being wired to the WS session.
            */}
          </>
        )}

        {/* Tick marks at the clip left edges (subtle) */}
        {clips.map((clip, i) => {
          const left = epochToPercent(clip.start_epoch, dayStartEpoch, dayEndEpoch);
          return (
            <div
              key={`tick-${i}`}
              className="pointer-events-none absolute top-0 h-2 w-px bg-accent/60"
              style={{ left: `${left}%` }}
            />
          );
        })}
      </div>

      {/* ── Hour axis ────────────────────────────────────────────────────── */}
      <div className="relative mt-1 h-4">
        {AXIS_HOURS.map((hour) => {
          const elapsedSec = hour * 3_600;
          // Position formula from spec: ((nvrHour × 3600) / dayDuration) × 100
          // Equivalent to epochToPercent(dayStart + elapsed, dayStart, dayEnd)
          const pct = dayDuration > 0 ? (elapsedSec / dayDuration) * 100 : 0;
          const epoch = dayStartEpoch + elapsedSec;
          // Format HH:MM (drop :SS for axis labels)
          const label = epochToNvrTimeStr(epoch, tzOffsetMinutes).slice(0, 5);
          return (
            <span
              key={hour}
              className="absolute -translate-x-1/2 text-[9px] leading-none text-ink-dim/50 select-none"
              style={{ left: `${pct}%` }}
            >
              {label}
            </span>
          );
        })}
      </div>
    </div>
  );
}
