/**
 * Timeline — zoomable recorded-footage bar with draggable playhead, hover cursor,
 * hour/minute ticks, recording segments and shift-drag range selection.
 *
 * Interaction model:
 *  - Click / drag on the track → scrub. The ghost playhead + time bubble follow the
 *    pointer; onSeek fires ONCE on pointerup (the 250 ms debounce + clamp-to-now
 *    live in PlaybackPage — Contract §7). Click-to-seek is a zero-length drag.
 *  - Shift-drag (or the "Select range" affordance) → paint an export selection.
 *    The band is clamped live via clampClipRange(...) to ≤10 min and ≤now; on
 *    release the committed {start,end} is emitted through onSelectionChange.
 *    The PAGE renders the actual "Export clip" button — Timeline only owns the
 *    visual selection.
 *  - Zoom: +/− buttons, a "Fit day" reset, and Ctrl/⌘+wheel over the track. Zoom
 *    scales the inner track wider than the viewport; the whole day stays reachable
 *    via native horizontal scroll. Current span is shown next to the buttons.
 *  - Hover (when not interacting) → thin cursor line + a small time tooltip.
 *  - role="slider" + keyboard: ←/→ = ±10 s, Home/End = first/last clip,
 *    PageUp/Down = prev/next recording. Prev/next buttons flank the track.
 *  - Drag/seek/select disabled when playerState === "error" (zoom stays enabled).
 */

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { useTranslation } from "react-i18next";
import type { RecordingClip } from "@/api/types";
import type { PlayerState } from "./types";
import {
  epochToNvrTimeStr,
  snapToNearest,
  clampClipRange,
  MAX_CLIP_SECONDS,
} from "./playback-utils";
import { ChevronRight, PlusIcon, XIcon } from "@/components/icons";

// ── Pure helpers (exported for Vitest) ────────────────────────────────────────

/**
 * Map a UTC epoch to a percentage position [0, 100] within a span.
 *
 * Uses `spanEnd - spanStart` as the divisor so 23h/25h DST days are handled
 * correctly — never hard-codes 86 400. Reused both for the full day (dayStart/
 * dayEnd) and, when zoomed, still spans the full day inside the widened track.
 */
export function epochToPercent(
  epoch: number,
  spanStart: number,
  spanEnd: number,
): number {
  const span = spanEnd - spanStart;
  if (span <= 0) return 0;
  return ((epoch - spanStart) / span) * 100;
}

/** Inverse of epochToPercent — map a percentage [0, 100] back to a UTC epoch. */
export function percentToEpoch(
  pct: number,
  spanStart: number,
  spanEnd: number,
): number {
  const span = spanEnd - spanStart;
  return spanStart + (pct / 100) * span;
}

// ── Zoom math (exported for Vitest) ───────────────────────────────────────────

/**
 * Visible span (seconds) for each zoom level ABOVE the full-day fit (index 0).
 * Index 0 always means "fit the whole day"; index 1..N pick a fixed window that
 * is clamped to the day's real duration. Progression: 12h → 1m.
 */
export const ZOOM_SPANS_SECONDS = [
  43_200, // 12h
  21_600, // 6h
  10_800, // 3h
  3_600, // 1h
  1_800, // 30m
  900, // 15m
  300, // 5m
  60, // 1m
] as const;

/** Deepest zoom index (index 0 = full-day fit). */
export const MAX_ZOOM_INDEX = ZOOM_SPANS_SECONDS.length;

/** Clamp a zoom index to the valid [0, MAX_ZOOM_INDEX] range. */
export function clampZoomIndex(index: number): number {
  return Math.max(0, Math.min(MAX_ZOOM_INDEX, Math.round(index)));
}

/**
 * Visible span in seconds for a zoom index.
 *  - index ≤ 0 → the whole day (fit).
 *  - index ≥ 1 → the fixed window at that step, never longer than the day.
 */
export function zoomSpanSeconds(zoomIndex: number, dayDuration: number): number {
  if (zoomIndex <= 0) return dayDuration;
  const i = Math.min(zoomIndex, ZOOM_SPANS_SECONDS.length);
  return Math.min(dayDuration, ZOOM_SPANS_SECONDS[i - 1]);
}

/** Horizontal scale factor (≥1) for a zoom index — track width = scale × viewport. */
export function zoomScale(zoomIndex: number, dayDuration: number): number {
  const span = zoomSpanSeconds(zoomIndex, dayDuration);
  return span > 0 ? dayDuration / span : 1;
}

/**
 * Choose a readable tick interval (seconds) for a given visible span so labels
 * never crowd. Coarser when zoomed out, down to 1-minute ticks when zoomed in.
 */
export function chooseTickInterval(span: number): number {
  if (span >= 12 * 3_600) return 3 * 3_600; // ≥12h → 3h
  if (span >= 6 * 3_600) return 3_600; // ≥6h → 1h
  if (span >= 3 * 3_600) return 1_800; // ≥3h → 30m
  if (span >= 3_600) return 900; // ≥1h → 15m
  if (span >= 1_800) return 300; // ≥30m → 5m
  if (span >= 600) return 120; // ≥10m → 2m
  return 60; // <10m → 1m
}

/** Compact span label: 86 400 → "24h", 3 600 → "1h", 1 800 → "30m", 90 → "1m". */
export function formatSpanLabel(span: number): string {
  const h = Math.floor(span / 3_600);
  const m = Math.round((span % 3_600) / 60);
  if (h && m) return `${h}h${m}m`;
  if (h) return `${h}h`;
  return `${Math.max(1, m)}m`;
}

/** Format a duration (seconds) as M:SS / MM:SS for the selection chip. */
export function formatDurationLabel(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  const mm = Math.floor(s / 60);
  const ss = s % 60;
  return `${mm}:${String(ss).padStart(2, "0")}`;
}

// ── Types ─────────────────────────────────────────────────────────────────────

interface TimelineProps {
  /** Day boundaries from RecordingIndex (UTC epoch seconds). */
  dayStartEpoch: number;
  dayEndEpoch: number;

  /** Merged recording spans for the day (drawn as segments). */
  clips: RecordingClip[];

  /** NVR timezone offset (minutes, same sign as backend). */
  tzOffsetMinutes: number;

  /** Current playhead position (footage epoch). Drives the playhead marker. */
  playheadEpoch: number | null;

  /**
   * Fired when the user COMMITS a seek (drag release / click / keyboard).
   * Timeline fires once — no internal debounce. The 250 ms debounce lives in
   * PlaybackPage, not here (Contract §7).
   */
  onSeek: (epoch: number) => void;

  /** Player state — drag/seek/select are disabled when playerState === "error". */
  playerState: PlayerState;

  /** Prev/next-recording notifications (Timeline's buttons + PageUp/Down). */
  onPrevClip?: () => void;
  onNextClip?: () => void;

  /**
   * NEW — emitted when the visual export selection changes (shift-drag paint or
   * clear). The range is already clamped to ≤maxClipSeconds and ≤now via
   * clampClipRange. `null` means the selection was cleared. The PAGE renders the
   * actual "Export clip" button + download; Timeline only owns the visual band.
   */
  onSelectionChange?: (range: { start: number; end: number } | null) => void;

  /** NEW — override the export cap (seconds). Defaults to MAX_CLIP_SECONDS (600). */
  maxClipSeconds?: number;
}

type Interaction =
  | { kind: "none" }
  | { kind: "scrub"; epoch: number }
  | { kind: "select"; anchor: number; current: number };

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
  onSelectionChange,
  maxClipSeconds = MAX_CLIP_SECONDS,
}: TimelineProps) {
  const { t } = useTranslation();
  const containerRef = useRef<HTMLDivElement>(null);
  const barRef = useRef<HTMLDivElement>(null);

  const dayDuration = dayEndEpoch - dayStartEpoch;
  const isDisabled = playerState === "error";

  // ── View / interaction state ────────────────────────────────────────────────
  const [zoomIndex, setZoomIndex] = useState(0);
  const [interaction, setInteraction] = useState<Interaction>({ kind: "none" });
  const [hoverEpoch, setHoverEpoch] = useState<number | null>(null);
  const [selectMode, setSelectMode] = useState(false);
  const [selection, setSelection] = useState<{ start: number; end: number } | null>(
    null,
  );
  const [scrollLeft, setScrollLeft] = useState(0);
  const [containerWidth, setContainerWidth] = useState(0);

  // Ref mirror so pointerup reads the freshest interaction without stale closures.
  const interactionRef = useRef<Interaction>(interaction);
  interactionRef.current = interaction;

  const scale = zoomScale(zoomIndex, dayDuration);
  const visibleSpan = zoomSpanSeconds(zoomIndex, dayDuration);
  const contentWidth = containerWidth * scale;

  // Epoch under a viewport x-coordinate — desired zoom centre.
  const pendingCenterRef = useRef<number | null>(null);

  // ── Commit a seek, clamped to "now" (HIGH-3) ─────────────────────────────────
  // The bar spans the whole day including the future part of "today"; never seek
  // past the present or the backend builds start>end and starves.
  const commitSeek = useCallback(
    (epoch: number) => {
      onSeek(Math.min(epoch, Math.floor(Date.now() / 1000)));
    },
    [onSeek],
  );

  // ── Pointer → epoch (accounts for scroll: barRef spans the full day) ─────────
  function pointerEpoch(clientX: number): number {
    const rect = barRef.current?.getBoundingClientRect();
    if (!rect || rect.width <= 0) return dayStartEpoch;
    const x = Math.max(0, Math.min(rect.width, clientX - rect.left));
    return percentToEpoch((x / rect.width) * 100, dayStartEpoch, dayEndEpoch);
  }

  // ── rAF-throttled pointer move ───────────────────────────────────────────────
  const frameRef = useRef<number | null>(null);
  const pendingXRef = useRef<number | null>(null);

  const applyMove = useCallback((clientX: number) => {
    const epoch = pointerEpoch(clientX);
    setHoverEpoch(epoch);
    setInteraction((prev) => {
      if (prev.kind === "scrub") return { kind: "scrub", epoch };
      if (prev.kind === "select") return { ...prev, current: epoch };
      return prev;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dayStartEpoch, dayEndEpoch]);

  const scheduleMove = useCallback(
    (clientX: number) => {
      pendingXRef.current = clientX;
      if (frameRef.current != null) return;
      frameRef.current = requestAnimationFrame(() => {
        frameRef.current = null;
        const x = pendingXRef.current;
        pendingXRef.current = null;
        if (x != null) applyMove(x);
      });
    },
    [applyMove],
  );

  useEffect(
    () => () => {
      if (frameRef.current != null) cancelAnimationFrame(frameRef.current);
    },
    [],
  );

  // ── Pointer handlers ─────────────────────────────────────────────────────────
  function handlePointerDown(e: React.PointerEvent<HTMLDivElement>) {
    if (isDisabled) return;
    barRef.current?.setPointerCapture(e.pointerId);
    const epoch = pointerEpoch(e.clientX);
    const next: Interaction =
      e.shiftKey || selectMode
        ? { kind: "select", anchor: epoch, current: epoch }
        : { kind: "scrub", epoch };
    interactionRef.current = next;
    setInteraction(next);
  }

  function handlePointerMove(e: React.PointerEvent<HTMLDivElement>) {
    if (isDisabled) return;
    scheduleMove(e.clientX);
  }

  function handlePointerUp(e: React.PointerEvent<HTMLDivElement>) {
    if (isDisabled) return;
    if (frameRef.current != null) {
      cancelAnimationFrame(frameRef.current);
      frameRef.current = null;
    }
    const it = interactionRef.current;
    const epoch = pointerEpoch(e.clientX);

    if (it.kind === "scrub") {
      const snapped = snapToNearest(clips, epoch);
      // Fires ONCE on release. Releasing past all clips is a no-op (no seek into
      // the void) — snapToNearest returns null there.
      if (snapped !== null) commitSeek(snapped);
    } else if (it.kind === "select") {
      const now = Math.floor(Date.now() / 1000);
      const range = clampClipRange(it.anchor, epoch, now, maxClipSeconds);
      if (range) {
        const sel = { start: range.start, end: range.end };
        setSelection(sel);
        onSelectionChange?.(sel);
      } else {
        setSelection(null);
        onSelectionChange?.(null);
      }
    }

    const none: Interaction = { kind: "none" };
    interactionRef.current = none;
    setInteraction(none);
  }

  function handlePointerLeave() {
    if (interactionRef.current.kind === "none") setHoverEpoch(null);
  }

  // ── Keyboard navigation (role="slider") ──────────────────────────────────────
  function handleKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
    if (isDisabled || clips.length === 0) return;
    const head = playheadEpoch ?? dayStartEpoch;

    switch (e.key) {
      case "ArrowLeft": {
        e.preventDefault();
        const snapped = snapToNearest(clips, Math.max(dayStartEpoch, head - 10));
        if (snapped !== null) commitSeek(snapped);
        break;
      }
      case "ArrowRight": {
        e.preventDefault();
        const snapped = snapToNearest(clips, Math.min(dayEndEpoch - 1, head + 10));
        if (snapped !== null) commitSeek(snapped);
        break;
      }
      case "Home":
        e.preventDefault();
        commitSeek(clips[0].start_epoch);
        break;
      case "End":
        e.preventDefault();
        commitSeek(clips[clips.length - 1].start_epoch);
        break;
      case "PageUp":
        e.preventDefault();
        seekToPrevClip();
        break;
      case "PageDown":
        e.preventDefault();
        seekToNextClip();
        break;
    }
  }

  // ── Prev / next recording ────────────────────────────────────────────────────
  function seekToPrevClip() {
    if (playheadEpoch === null) return;
    let prevClip: RecordingClip | null = null;
    for (const clip of clips) {
      if (clip.end_epoch < playheadEpoch) prevClip = clip;
    }
    if (prevClip) {
      commitSeek(prevClip.start_epoch);
      onPrevClip?.();
    }
  }

  function seekToNextClip() {
    if (playheadEpoch === null) return;
    for (const clip of clips) {
      if (clip.start_epoch > playheadEpoch) {
        commitSeek(clip.start_epoch);
        onNextClip?.();
        return;
      }
    }
  }

  // ── Zoom ─────────────────────────────────────────────────────────────────────
  const viewportCenterEpoch = useCallback((): number => {
    const c = containerRef.current;
    if (!c || c.scrollWidth <= 0) return playheadEpoch ?? dayStartEpoch;
    const centerX = c.scrollLeft + c.clientWidth / 2;
    return percentToEpoch((centerX / c.scrollWidth) * 100, dayStartEpoch, dayEndEpoch);
  }, [dayStartEpoch, dayEndEpoch, playheadEpoch]);

  const doZoom = useCallback(
    (delta: number, anchorClientX: number | null) => {
      let anchorEpoch: number | null = null;
      const rect = barRef.current?.getBoundingClientRect();
      if (anchorClientX != null && rect && rect.width > 0) {
        const x = Math.max(0, Math.min(rect.width, anchorClientX - rect.left));
        anchorEpoch = percentToEpoch((x / rect.width) * 100, dayStartEpoch, dayEndEpoch);
      }
      pendingCenterRef.current = anchorEpoch ?? viewportCenterEpoch();
      setZoomIndex((prev) => clampZoomIndex(prev + delta));
    },
    [dayStartEpoch, dayEndEpoch, viewportCenterEpoch],
  );

  function resetZoom() {
    pendingCenterRef.current = null;
    setZoomIndex(0);
    if (containerRef.current) containerRef.current.scrollLeft = 0;
    setScrollLeft(0);
  }

  // Ctrl/⌘+wheel zoom — native listener so we can preventDefault (React wheel is
  // passive). Re-bound when the day bounds change so epoch math stays fresh.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    function onWheel(e: WheelEvent) {
      if (!(e.ctrlKey || e.metaKey)) return;
      e.preventDefault();
      doZoom(e.deltaY < 0 ? 1 : -1, e.clientX);
    }
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [doZoom]);

  // After a zoom changes the track width, recentre the viewport on the anchor.
  useLayoutEffect(() => {
    const c = containerRef.current;
    if (!c) return;
    const centerEpoch = pendingCenterRef.current;
    if (centerEpoch == null) return;
    pendingCenterRef.current = null;
    const contentW = c.scrollWidth;
    if (contentW <= 0) return;
    const pct = epochToPercent(centerEpoch, dayStartEpoch, dayEndEpoch) / 100;
    const next = Math.max(0, Math.min(contentW - c.clientWidth, pct * contentW - c.clientWidth / 2));
    c.scrollLeft = next;
    setScrollLeft(next);
  }, [scale, dayStartEpoch, dayEndEpoch]);

  // ── Track measurement + scroll tracking (for tick windowing) ─────────────────
  useLayoutEffect(() => {
    const c = containerRef.current;
    if (!c) return;
    setContainerWidth(c.clientWidth);
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => setContainerWidth(c.clientWidth));
    ro.observe(c);
    return () => ro.disconnect();
  }, []);

  const scrollFrameRef = useRef<number | null>(null);
  function handleScroll() {
    const c = containerRef.current;
    if (!c) return;
    if (scrollFrameRef.current != null) return;
    scrollFrameRef.current = requestAnimationFrame(() => {
      scrollFrameRef.current = null;
      if (containerRef.current) setScrollLeft(containerRef.current.scrollLeft);
    });
  }

  // ── Derived render values ────────────────────────────────────────────────────
  const now = Math.floor(Date.now() / 1000);

  const playheadPct =
    playheadEpoch !== null
      ? epochToPercent(playheadEpoch, dayStartEpoch, dayEndEpoch)
      : null;

  const scrubEpoch = interaction.kind === "scrub" ? interaction.epoch : null;
  const scrubPct =
    scrubEpoch !== null
      ? epochToPercent(scrubEpoch, dayStartEpoch, dayEndEpoch)
      : null;

  const hoverPct =
    interaction.kind === "none" && hoverEpoch !== null
      ? epochToPercent(hoverEpoch, dayStartEpoch, dayEndEpoch)
      : null;

  // Live selection band while shift-dragging (clamped to the export cap + now).
  const liveSel =
    interaction.kind === "select"
      ? clampClipRange(interaction.anchor, interaction.current, now, maxClipSeconds)
      : null;
  const renderedSel = liveSel ?? selection;

  // Visible epoch window (from scroll) — bounds tick generation so deep zoom
  // never renders thousands of DOM nodes.
  const visStart =
    contentWidth > 0
      ? percentToEpoch((scrollLeft / contentWidth) * 100, dayStartEpoch, dayEndEpoch)
      : dayStartEpoch;
  const visEnd =
    contentWidth > 0
      ? percentToEpoch(
          ((scrollLeft + containerWidth) / contentWidth) * 100,
          dayStartEpoch,
          dayEndEpoch,
        )
      : dayEndEpoch;

  const tickInterval = chooseTickInterval(visibleSpan);
  const ticks: number[] = [];
  {
    const start =
      Math.floor((visStart - dayStartEpoch) / tickInterval) * tickInterval +
      dayStartEpoch;
    for (let e = start; e <= visEnd + tickInterval; e += tickInterval) {
      if (e < dayStartEpoch || e > dayEndEpoch) continue;
      ticks.push(e);
      if (ticks.length > 400) break; // hard safety cap
    }
  }

  const navDisabled = isDisabled || playheadEpoch === null || clips.length === 0;

  // ── Render ────────────────────────────────────────────────────────────────────
  return (
    <div className="relative flex select-none flex-col px-8">
      {/* ── Controls row ─────────────────────────────────────────────────────── */}
      <div className="mb-1.5 flex flex-wrap items-center gap-2">
        {/* Zoom cluster */}
        <div className="flex items-center gap-1">
          <button
            type="button"
            aria-label={t("playback.zoomOut")}
            title={t("playback.zoomOut")}
            disabled={zoomIndex <= 0}
            onClick={() => doZoom(-1, null)}
            className="flex h-6 w-6 items-center justify-center rounded text-ink-dim transition hover:bg-white/[.06] hover:text-ink-soft disabled:cursor-not-allowed disabled:opacity-30"
          >
            <MinusGlyph size={15} />
          </button>
          <button
            type="button"
            aria-label={t("playback.zoomIn")}
            title={t("playback.zoomIn")}
            disabled={zoomIndex >= MAX_ZOOM_INDEX}
            onClick={() => doZoom(1, null)}
            className="flex h-6 w-6 items-center justify-center rounded text-ink-dim transition hover:bg-white/[.06] hover:text-ink-soft disabled:cursor-not-allowed disabled:opacity-30"
          >
            <PlusIcon size={15} />
          </button>
          <span className="ml-1 font-mono text-[10px] tabular-nums text-ink-mute">
            {t("playback.zoomSpan", { span: formatSpanLabel(visibleSpan) })}
          </span>
          {zoomIndex > 0 && (
            <button
              type="button"
              onClick={resetZoom}
              className="ml-1 rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-ink-dim transition hover:bg-white/[.06] hover:text-ink-soft"
            >
              {t("playback.resetZoom")}
            </button>
          )}
        </div>

        <div className="flex-1" />

        {/* Selection cluster */}
        <button
          type="button"
          aria-label={t("playback.selectRange")}
          aria-pressed={selectMode}
          title={t("playback.selectRangeHint")}
          disabled={isDisabled}
          onClick={() => setSelectMode((v) => !v)}
          className={[
            "rounded px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide transition disabled:cursor-not-allowed disabled:opacity-30",
            selectMode
              ? "bg-accent/[.18] text-accent-light ring-1 ring-accent/30"
              : "text-ink-dim hover:bg-white/[.06] hover:text-ink-soft",
          ].join(" ")}
        >
          {t("playback.selectRange")}
        </button>
        {selection && (
          <div className="flex items-center gap-1 rounded bg-accent/[.12] px-2 py-0.5 ring-1 ring-accent/25">
            <span className="font-mono text-[10px] tabular-nums text-accent-light">
              {t("playback.selectionRange", {
                start: epochToNvrTimeStr(selection.start, tzOffsetMinutes),
                end: epochToNvrTimeStr(selection.end, tzOffsetMinutes),
                duration: formatDurationLabel(selection.end - selection.start),
              })}
            </span>
            <button
              type="button"
              aria-label={t("playback.clearSelection")}
              title={t("playback.clearSelection")}
              onClick={() => {
                setSelection(null);
                onSelectionChange?.(null);
              }}
              className="flex h-4 w-4 items-center justify-center rounded text-ink-dim transition hover:text-ink-soft"
            >
              <XIcon size={12} />
            </button>
          </div>
        )}
      </div>

      {/* ── Track region (prev/next flank the scroll viewport) ───────────────── */}
      <div className="relative">
        <button
          aria-label={t("playback.previousClip")}
          title={t("playback.previousClip")}
          disabled={navDisabled}
          onClick={seekToPrevClip}
          className="absolute -left-7 top-0 z-20 flex h-14 w-6 items-center justify-center text-ink-dim transition hover:text-ink-soft disabled:cursor-not-allowed disabled:opacity-30"
        >
          <ChevronRight size={18} className="rotate-180" />
        </button>
        <button
          aria-label={t("playback.nextClip")}
          title={t("playback.nextClip")}
          disabled={navDisabled}
          onClick={seekToNextClip}
          className="absolute -right-7 top-0 z-20 flex h-14 w-6 items-center justify-center text-ink-dim transition hover:text-ink-soft disabled:cursor-not-allowed disabled:opacity-30"
        >
          <ChevronRight size={18} />
        </button>

        {/* Scroll viewport — widened track scrolls horizontally when zoomed. */}
        <div
          ref={containerRef}
          onScroll={handleScroll}
          className={[
            "overflow-x-auto overflow-y-hidden rounded-md bg-[#0c1014]",
            zoomIndex > 0 ? "[scrollbar-width:thin]" : "",
          ].join(" ")}
        >
          {/* Width wrapper (track + axis share the same scaled width). */}
          <div style={{ width: `${scale * 100}%`, minWidth: "100%" }}>
            {/* Main bar (role="slider") */}
            <div
              ref={barRef}
              role="slider"
              aria-valuemin={dayStartEpoch}
              aria-valuemax={dayEndEpoch}
              aria-valuenow={playheadEpoch ?? dayStartEpoch}
              aria-valuetext={
                playheadEpoch !== null
                  ? epochToNvrTimeStr(playheadEpoch, tzOffsetMinutes)
                  : t("playback.noPosition")
              }
              aria-disabled={isDisabled}
              tabIndex={0}
              className={[
                "relative h-14",
                isDisabled
                  ? "cursor-not-allowed opacity-50"
                  : selectMode
                  ? "cursor-cell"
                  : "cursor-crosshair",
                "focus:outline-none focus-visible:ring-1 focus-visible:ring-accent/50",
              ].join(" ")}
              onPointerDown={handlePointerDown}
              onPointerMove={handlePointerMove}
              onPointerUp={handlePointerUp}
              onPointerLeave={handlePointerLeave}
              onKeyDown={handleKeyDown}
            >
              {/* Recording segments */}
              {clips.map((clip, i) => {
                const left = epochToPercent(clip.start_epoch, dayStartEpoch, dayEndEpoch);
                const right = epochToPercent(clip.end_epoch, dayStartEpoch, dayEndEpoch);
                const width = Math.max(0, right - left);
                return (
                  <div
                    key={i}
                    className="absolute top-2 h-10 rounded-sm bg-accent/30 ring-1 ring-inset ring-accent/25"
                    style={{ left: `${left}%`, width: `${width}%` }}
                  />
                );
              })}

              {/* Clip-start ticks */}
              {clips.map((clip, i) => {
                const left = epochToPercent(clip.start_epoch, dayStartEpoch, dayEndEpoch);
                return (
                  <div
                    key={`ct-${i}`}
                    className="pointer-events-none absolute top-2 h-2.5 w-px bg-accent-bright/70"
                    style={{ left: `${left}%` }}
                  />
                );
              })}

              {/* Selection band */}
              {renderedSel && (
                <div
                  className="pointer-events-none absolute top-0 h-full border-x border-accent bg-accent/25"
                  style={{
                    left: `${epochToPercent(renderedSel.start, dayStartEpoch, dayEndEpoch)}%`,
                    width: `${Math.max(
                      0,
                      epochToPercent(renderedSel.end, dayStartEpoch, dayEndEpoch) -
                        epochToPercent(renderedSel.start, dayStartEpoch, dayEndEpoch),
                    )}%`,
                  }}
                />
              )}

              {/* Hover cursor + tooltip (only when idle) */}
              {hoverPct !== null && (
                <>
                  <div
                    className="pointer-events-none absolute top-0 h-full w-px bg-white/25"
                    style={{ left: `${hoverPct}%` }}
                  />
                  <div
                    className="pointer-events-none absolute bottom-1 -translate-x-1/2 whitespace-nowrap rounded bg-black/80 px-1 py-px font-mono text-[9px] text-white/80"
                    style={{ left: `${hoverPct}%` }}
                  >
                    {epochToNvrTimeStr(hoverEpoch!, tzOffsetMinutes)}
                  </div>
                </>
              )}

              {/* Playhead marker + knob — hidden while scrubbing */}
              {playheadPct !== null && interaction.kind !== "scrub" && (
                <div
                  className="pointer-events-none absolute top-0 h-full"
                  style={{ left: `${playheadPct}%` }}
                >
                  <div className="absolute top-0 h-full w-0.5 -translate-x-1/2 bg-white shadow-[0_0_6px_rgba(255,255,255,0.6)]" />
                  <div className="absolute -top-1 h-2.5 w-2.5 -translate-x-1/2 rounded-full bg-white shadow-[0_0_4px_rgba(255,255,255,0.7)]" />
                </div>
              )}

              {/* Scrub ghost + time bubble */}
              {scrubPct !== null && (
                <>
                  <div
                    className="pointer-events-none absolute top-0 h-full w-0.5 -translate-x-1/2 bg-white/50"
                    style={{ left: `${scrubPct}%` }}
                  />
                  <div
                    className="pointer-events-none absolute top-1 -translate-x-1/2 whitespace-nowrap rounded bg-black/85 px-1.5 py-0.5 font-mono text-[10px] text-white/90"
                    style={{ left: `${scrubPct}%` }}
                  >
                    {epochToNvrTimeStr(scrubEpoch!, tzOffsetMinutes)}
                  </div>
                </>
              )}
            </div>

            {/* Hour / minute axis (scrolls with the track) */}
            <div className="relative mt-1 h-4">
              {ticks.map((epoch) => {
                const pct = epochToPercent(epoch, dayStartEpoch, dayEndEpoch);
                const label = epochToNvrTimeStr(epoch, tzOffsetMinutes).slice(0, 5);
                return (
                  <div key={epoch} className="absolute top-0" style={{ left: `${pct}%` }}>
                    <div className="h-1 w-px -translate-x-1/2 bg-ink-dim/40" />
                    <span className="absolute top-1 -translate-x-1/2 whitespace-nowrap text-[10px] leading-none text-ink-dim">
                      {label}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </div>

      {/* Clock caption — operators must know which clock the axis is in. */}
      <div className="mt-0.5 select-none text-right text-[10px] font-medium uppercase tracking-wide text-ink-faint">
        {t("playback.nvrLocalTime")}
      </div>
    </div>
  );
}

// ── Local minus glyph (icons.tsx has PlusIcon but no MinusIcon) ───────────────
function MinusGlyph({ size = 16 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      aria-hidden="true"
    >
      <path d="M5 12h14" />
    </svg>
  );
}
