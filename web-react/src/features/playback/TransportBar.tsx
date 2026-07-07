/**
 * TransportBar — the page-level playback control bar mounted under the player by
 * PlaybackPage. It drives play/pause through the player's imperative controlsRef
 * (never re-implementing the WS/reducer) and commits seeks through the page's
 * debounced `onSeek` (the 250 ms debounce + clamp-to-now stay in PlaybackPage /
 * this bar — never touching PlaybackPlayer's MSE seek/anchor/speed logic).
 *
 * Controls:
 *  - Play / pause (replaces the old floating in-video pause button).
 *  - Step −10 s / +10 s via stepEpoch() → snapToNearest() → onSeek.
 *  - Jump to the first / last recording of the day.
 *  - A big NVR-local footage-time readout (date + HH:MM:SS) from the playhead.
 *  - A speed indicator that cycles 1 → 2 → 4 → 8 → 1.
 *  - Keyboard: Space = play/pause, ←/→ = ±10 s, Home/End = jump. Ignored while
 *    typing in an input/select/textarea, and deferred to the Timeline slider when
 *    it owns focus (so arrows/Home/End aren't handled twice).
 */
import { useCallback, useEffect } from "react";
import { useTranslation } from "react-i18next";
import type { RecordingClip } from "@/api/types";
import type { PlayerState } from "./types";
import type { PlaybackControls } from "./PlaybackPlayer";
import { stepEpoch, snapToNearest, formatNvrDatetime } from "./playback-utils";
import { PlayIcon, PauseIcon } from "@/components/icons";

type Speed = 1 | 2 | 4 | 8;
const SPEEDS: readonly Speed[] = [1, 2, 4, 8] as const;
const STEP_SECONDS = 10;

export interface TransportBarProps {
  /** Current footage-time playhead (epoch seconds) from the player, or null. */
  playheadEpoch: number | null;
  dayStartEpoch: number;
  dayEndEpoch: number;
  /** Merged recording spans for the day (for snap + jump-to-start/end). */
  clips: RecordingClip[];
  tzOffsetMinutes: number;
  speed: Speed;
  onSpeedChange: (s: Speed) => void;
  /** Page-level committed seek (debounced 250 ms in PlaybackPage). */
  onSeek: (epoch: number) => void;
  playerState: PlayerState;
  /** Imperative play/pause populated by PlaybackPlayer once it mounts. */
  controlsRef: React.RefObject<PlaybackControls | null>;
}

/** True when a keydown originates from a control that owns the key itself. */
function shouldIgnoreKey(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el) return false;
  const tag = el.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable)
    return true;
  // The Timeline slider handles ←/→/Home/End itself when focused — don't double-seek.
  if (el.getAttribute?.("role") === "slider") return true;
  // A focused button (calendar day, toolbar control, popover item) owns Space/Enter
  // and shouldn't have the arrows/Home/End trigger a seek behind it (e.g. the
  // recordings-calendar popover is open) — defer all transport keys to it.
  if (tag === "BUTTON") return true;
  return false;
}

export default function TransportBar({
  playheadEpoch,
  dayStartEpoch,
  dayEndEpoch,
  clips,
  tzOffsetMinutes,
  speed,
  onSpeedChange,
  onSeek,
  playerState,
  controlsRef,
}: TransportBarProps) {
  const { t } = useTranslation();

  const isPlaying = playerState === "playing";
  const isBusy = playerState === "loading" || playerState === "seeking";
  const hasClips = clips.length > 0;

  /** Commit a seek clamped to "now" and snapped to covered footage. */
  const emitSeek = useCallback(
    (epoch: number) => {
      const now = Math.floor(Date.now() / 1000);
      const snapped = snapToNearest(clips, Math.min(epoch, now));
      if (snapped !== null) onSeek(snapped);
    },
    [clips, onSeek],
  );

  const head = playheadEpoch ?? clips[0]?.start_epoch ?? dayStartEpoch;

  const stepBack = useCallback(
    () => emitSeek(stepEpoch(head, -STEP_SECONDS, dayStartEpoch, dayEndEpoch)),
    [emitSeek, head, dayStartEpoch, dayEndEpoch],
  );
  const stepForward = useCallback(
    () => emitSeek(stepEpoch(head, STEP_SECONDS, dayStartEpoch, dayEndEpoch)),
    [emitSeek, head, dayStartEpoch, dayEndEpoch],
  );
  const jumpStart = useCallback(() => {
    if (clips.length > 0) onSeek(clips[0].start_epoch);
  }, [clips, onSeek]);
  const jumpEnd = useCallback(() => {
    if (clips.length === 0) return;
    const last = clips[clips.length - 1];
    // Land INSIDE the last clip (end_epoch is exclusive); clamp-to-now handled in emitSeek.
    emitSeek(last.end_epoch - 1);
  }, [clips, emitSeek]);

  const togglePlay = useCallback(() => {
    const c = controlsRef.current;
    if (!c) return;
    if (isPlaying) c.pause();
    else c.play();
  }, [controlsRef, isPlaying]);

  const cycleSpeed = useCallback(() => {
    const idx = SPEEDS.indexOf(speed);
    onSpeedChange(SPEEDS[(idx + 1) % SPEEDS.length]);
  }, [speed, onSpeedChange]);

  // ── Keyboard shortcuts (window-level; ignored while typing / on the slider) ──
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (shouldIgnoreKey(e.target)) return;
      switch (e.key) {
        case " ":
        case "Spacebar":
          e.preventDefault();
          togglePlay();
          break;
        case "ArrowLeft":
          e.preventDefault();
          stepBack();
          break;
        case "ArrowRight":
          e.preventDefault();
          stepForward();
          break;
        case "Home":
          e.preventDefault();
          jumpStart();
          break;
        case "End":
          e.preventDefault();
          jumpEnd();
          break;
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [togglePlay, stepBack, stepForward, jumpStart, jumpEnd]);

  // ── Footage-time readout (NVR-local) ─────────────────────────────────────────
  const readout =
    playheadEpoch != null ? formatNvrDatetime(playheadEpoch, tzOffsetMinutes) : null;
  const [datePart, timePart] = readout ? readout.split(" ") : [null, null];

  const btn =
    "flex h-9 w-9 items-center justify-center rounded-md text-ink-dim transition hover:bg-white/[.06] hover:text-ink-soft disabled:cursor-not-allowed disabled:opacity-30";

  return (
    <div className="flex flex-none items-center gap-2 border-t border-white/[.06] bg-[#0c1014] px-4 py-2">
      {/* Jump to first recording */}
      <button
        type="button"
        aria-label={t("playback.jumpToStart")}
        title={t("playback.jumpToStart")}
        disabled={!hasClips}
        onClick={jumpStart}
        className={btn}
      >
        <JumpGlyph size={16} />
      </button>

      {/* Step back 10 s */}
      <button
        type="button"
        aria-label={t("playback.stepBack")}
        title={t("playback.stepBack")}
        disabled={!hasClips}
        onClick={stepBack}
        className={btn}
      >
        <StepGlyph size={16} />
      </button>

      {/* Play / pause */}
      <button
        type="button"
        aria-label={isPlaying ? t("playback.pausePlayback") : t("playback.resumePlayback")}
        title={isPlaying ? t("playback.pausePlayback") : t("playback.resumePlayback")}
        aria-pressed={isPlaying}
        disabled={isBusy || !controlsRef.current}
        onClick={togglePlay}
        className="flex h-9 w-9 items-center justify-center rounded-full bg-white/10 text-ink-soft ring-1 ring-white/15 transition hover:bg-white/[.16] disabled:cursor-not-allowed disabled:opacity-40"
      >
        {isPlaying ? <PauseIcon size={17} /> : <PlayIcon size={17} />}
      </button>

      {/* Step forward 10 s */}
      <button
        type="button"
        aria-label={t("playback.stepForward")}
        title={t("playback.stepForward")}
        disabled={!hasClips}
        onClick={stepForward}
        className={btn}
      >
        <StepGlyph size={16} className="rotate-180" />
      </button>

      {/* Jump to last recording */}
      <button
        type="button"
        aria-label={t("playback.jumpToEnd")}
        title={t("playback.jumpToEnd")}
        disabled={!hasClips}
        onClick={jumpEnd}
        className={btn}
      >
        <JumpGlyph size={16} className="rotate-180" />
      </button>

      {/* Big footage-time readout */}
      <div
        className="ml-3 flex flex-col leading-none"
        aria-label={t("playback.currentFootageTime")}
        data-testid="transport-readout"
      >
        <span className="font-mono text-lg font-semibold tabular-nums text-ink-soft">
          {timePart ?? "--:--:--"}
        </span>
        <span className="font-mono text-[10px] tabular-nums text-ink-dim">
          {datePart ?? "—"}
        </span>
      </div>

      <div className="flex-1" />

      {/* Speed cycle indicator/control */}
      <button
        type="button"
        aria-label={t("playback.cyclePlaybackSpeed", { speed })}
        title={t("playback.cyclePlaybackSpeed", { speed })}
        onClick={cycleSpeed}
        className="flex h-9 min-w-[3rem] items-center justify-center gap-1 rounded-md bg-accent/[.14] px-3 text-sm font-semibold text-accent-light ring-1 ring-accent/25 transition hover:bg-accent/[.2]"
      >
        {t("playback.speedLabel", { speed })}
      </button>
    </div>
  );
}

// ── Local glyphs ────────────────────────────────────────────────────────────────

/** Skip / step arrow (points left; rotate-180 for forward). */
function StepGlyph({ size = 16, className }: { size?: number; className?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      <path d="M11 19l-7-7 7-7" />
      <path d="M20 19l-7-7 7-7" />
    </svg>
  );
}

/** Jump-to-edge glyph (bar + double chevron; points left, rotate-180 for end). */
function JumpGlyph({ size = 16, className }: { size?: number; className?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      <path d="M18 18l-6-6 6-6" />
      <path d="M11 18l-6-6 6-6" />
      <path d="M5 5v14" />
    </svg>
  );
}
