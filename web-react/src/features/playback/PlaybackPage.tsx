import { useState } from "react";
import {
  useNvrs,
  useCameras,
  useRecordingAvailability,
  useRecordingIndex,
} from "@/api/hooks";
import { CameraIcon } from "@/components/icons";

type Speed = 1 | 2 | 4 | 8;

/** Today as "YYYY-MM-DD" (UTC). The NVR tz offset is applied to the min/max
 *  constraints once `indexData.tz_offset_minutes` is available. */
function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

/**
 * PlaybackPage — NVR → camera → date selectors, speed controls, and snapshot.
 * Player (Task 14) and Timeline (Task 13) are placeholder divs here; their
 * eventual props are documented in comments below so the swap is mechanical.
 */
export default function PlaybackPage() {
  const [selectedNvrId, setSelectedNvrId] = useState<string | null>(null);
  const [selectedCamId, setSelectedCamId] = useState<string | null>(null);
  const [selectedDate, setSelectedDate] = useState<string>(todayIso());
  /** "YYYY-MM" — tracks the month visible in the date picker for availability. */
  const [viewMonth, setViewMonth] = useState<string>(todayIso().slice(0, 7));
  /** Footage epoch (UTC seconds) committed by the Timeline on drag-release. */
  const [seekTarget, setSeekTarget] = useState<number | null>(null);
  const [speed, setSpeed] = useState<Speed>(1);

  // ── Data ────────────────────────────────────────────────────────────────────

  const { data: nvrs = [] } = useNvrs();
  const { data: allCameras = [] } = useCameras();

  /** Cameras belonging to the selected NVR, enabled, sorted by channel. */
  const cameras = allCameras
    .filter((c) => c.nvr_id === selectedNvrId && c.enabled)
    .sort((a, b) => a.channel - b.channel);

  const selectedCam = cameras.find((c) => c.id === selectedCamId) ?? null;
  /** 1-based channel passed to playback hooks (Contract #9 / channel 1-based). */
  const channel = selectedCam?.channel ?? 0;

  const hasSelection = !!selectedNvrId && channel > 0;

  const { data: availabilityData } = useRecordingAvailability(
    selectedNvrId ?? "",
    channel,
    viewMonth,
    hasSelection,
  );

  const { data: indexData } = useRecordingIndex(
    selectedNvrId ?? "",
    channel,
    selectedDate,
    hasSelection && !!selectedDate,
  );

  // ── Handlers ────────────────────────────────────────────────────────────────

  function handleNvrChange(nvrId: string) {
    setSelectedNvrId(nvrId || null);
    // Changing NVR resets camera and date (brief requirement)
    setSelectedCamId(null);
    const today = todayIso();
    setSelectedDate(today);
    setViewMonth(today.slice(0, 7));
    setSeekTarget(null);
  }

  function handleCamChange(camId: string) {
    setSelectedCamId(camId || null);
    setSeekTarget(null);
  }

  function handleDateChange(e: React.ChangeEvent<HTMLInputElement>) {
    const val = e.target.value; // "YYYY-MM-DD"
    setSelectedDate(val);
    // Keep viewMonth in sync so availability refetches for the visible month
    if (val.length >= 7) setViewMonth(val.slice(0, 7));
    setSeekTarget(null);
  }

  // ── Derived ─────────────────────────────────────────────────────────────────

  /** Oldest available date from NVR retention data — used as min for date picker. */
  const oldestDate: string | null =
    availabilityData?.oldest_epoch != null
      ? new Date(availabilityData.oldest_epoch * 1000).toISOString().slice(0, 10)
      : null;

  const maxDate = todayIso();

  /**
   * Task 14 sets this to true once PlaybackPlayer fires its onReady callback.
   * Snapshot remains disabled until then (brief §"Snapshot button").
   */
  const playerReady = false;

  // ── Render ──────────────────────────────────────────────────────────────────

  return (
    <div className="flex h-full flex-col overflow-hidden bg-bg">
      {/* ── Toolbar ─────────────────────────────────────────────────────────── */}
      <div className="flex flex-none flex-wrap items-end gap-3 border-b border-white/[.06] bg-[#0c1014] px-4 py-2">
        {/* NVR selector */}
        <div className="flex flex-col gap-0.5">
          <label
            htmlFor="pb-nvr"
            className="text-[10px] font-semibold uppercase tracking-wider text-ink-dim"
          >
            NVR
          </label>
          <select
            id="pb-nvr"
            aria-label="NVR"
            value={selectedNvrId ?? ""}
            onChange={(e) => handleNvrChange(e.target.value)}
            className="h-8 rounded-md border border-white/[.08] bg-[#161b22] px-2 text-sm text-ink-soft focus:outline-none focus:ring-1 focus:ring-accent/50"
          >
            <option value="">— select NVR —</option>
            {nvrs.map((nvr) => (
              <option key={nvr.id} value={nvr.id}>
                {nvr.label}
              </option>
            ))}
          </select>
        </div>

        {/* Camera selector */}
        <div className="flex flex-col gap-0.5">
          <label
            htmlFor="pb-cam"
            className="text-[10px] font-semibold uppercase tracking-wider text-ink-dim"
          >
            Camera
          </label>
          <select
            id="pb-cam"
            aria-label="Camera"
            value={selectedCamId ?? ""}
            onChange={(e) => handleCamChange(e.target.value)}
            disabled={!selectedNvrId || cameras.length === 0}
            className="h-8 rounded-md border border-white/[.08] bg-[#161b22] px-2 text-sm text-ink-soft focus:outline-none focus:ring-1 focus:ring-accent/50 disabled:opacity-40"
          >
            <option value="">— select camera —</option>
            {cameras.map((cam) => (
              <option key={cam.id} value={cam.id}>
                {cam.display_name} ch{cam.channel}
              </option>
            ))}
          </select>
        </div>

        {/* Date picker */}
        <div className="flex flex-col gap-0.5">
          <label
            htmlFor="pb-date"
            className="text-[10px] font-semibold uppercase tracking-wider text-ink-dim"
          >
            Date
          </label>
          <input
            id="pb-date"
            type="date"
            aria-label="Date"
            value={selectedDate}
            min={oldestDate ?? undefined}
            max={maxDate}
            disabled={!selectedCamId}
            onChange={handleDateChange}
            className="h-8 rounded-md border border-white/[.08] bg-[#161b22] px-2 text-sm text-ink-soft focus:outline-none focus:ring-1 focus:ring-accent/50 disabled:opacity-40"
          />
          {oldestDate && (
            <span className="mt-0.5 text-[10px] text-ink-dim">
              Oldest recording: {oldestDate}
            </span>
          )}
        </div>

        {/* Spacer */}
        <div className="flex-1" />

        {/* Speed selector — server-side decimation; <video>.playbackRate stays 1.0 (Contract #13) */}
        <div className="flex items-center gap-1" aria-label="Playback speed">
          {([1, 2, 4, 8] as const).map((s) => (
            <button
              key={s}
              aria-label={`${s}× speed`}
              aria-pressed={speed === s}
              onClick={() => setSpeed(s)}
              className={[
                "h-8 rounded-md px-3 text-sm font-semibold transition",
                speed === s
                  ? "bg-accent/[.18] text-accent-light ring-1 ring-accent/30"
                  : "text-ink-dim hover:bg-white/[.05] hover:text-ink-soft",
              ].join(" ")}
            >
              {s}×
            </button>
          ))}
        </div>

        {/* Snapshot — disabled until Task 15 hook + Task 14 player-ready */}
        <button
          aria-label="Snapshot"
          disabled={!playerReady}
          title={playerReady ? "Take snapshot" : "Start playback first"}
          className="flex h-8 items-center gap-1.5 rounded-md px-3 text-sm font-semibold text-ink-dim transition hover:bg-white/[.05] hover:text-ink-soft disabled:cursor-not-allowed disabled:opacity-40"
        >
          <CameraIcon size={15} />
          Snapshot
        </button>
      </div>

      {/* ── Player area ─────────────────────────────────────────────────────── */}
      {/* data-seek-target exposes seekTarget for Task 14 swap and test assertions */}
      <div
        className="relative min-h-0 flex-1 bg-black"
        data-testid="player-placeholder"
        data-seek-target={seekTarget ?? ""}
      >
        {/*
          Task 14 — swap this div for:
            <PlaybackPlayer
              nvrId={selectedNvrId}
              channel={channel}
              indexData={indexData}
              seekTarget={seekTarget}
              speed={speed}
              onSeek={(epoch) => setSeekTarget(epoch)}
              onReady={() => setPlayerReady(true)}
            />
        */}
        <div className="flex h-full items-center justify-center text-sm text-ink-dim/50">
          {!selectedNvrId
            ? "Select an NVR to start"
            : !selectedCamId
            ? "Select a camera"
            : !selectedDate
            ? "Select a date"
            : indexData && indexData.clips.length === 0
            ? "No coverage for this day"
            : "player here"}
        </div>
      </div>

      {/* ── Timeline ────────────────────────────────────────────────────────── */}
      <div
        className="flex h-20 flex-none items-center justify-center border-t border-white/[.06] bg-[#0c1014] text-xs text-ink-dim/40"
        data-testid="timeline-placeholder"
      >
        {/*
          Task 13 — swap this div for:
            <Timeline
              indexData={indexData}
              seekTarget={seekTarget}
              onSeek={(epoch) => setSeekTarget(epoch)}
            />
        */}
        timeline here
      </div>
    </div>
  );
}
