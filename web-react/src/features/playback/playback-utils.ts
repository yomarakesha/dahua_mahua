/**
 * Pure playback utility functions — no side effects, fully unit-testable.
 *
 * Epoch convention throughout: UTC seconds (integer or float).
 * NVR timezone convention: NVR local = UTC + tz_offset_minutes (fixed offset, no DST).
 */
import { CONFIG } from "@/lib/config";
import type { FootageAnchor } from "./types";
import type { RecordingClip } from "@/api/types";

// ── Playback WebSocket URL ──────────────────────────────────────────────────────

/**
 * Swap an http(s) origin to its ws(s) equivalent (only the leading scheme).
 * `https://…` → `wss://…`, `http://…` → `ws://…`.  Exported for unit testing.
 */
export function httpToWsBase(httpBase: string): string {
  return httpBase.replace(/^http/, "ws");
}

/**
 * Build the playback streaming WebSocket URL.
 *
 * CONFIG.backendBase already encodes the right origin for the page protocol:
 *   HTTPS (Caddy :8443):  https://host:8443/api/v1  → wss://host:8443/api/v1/…
 *   HTTP  (dev :8000):    http://host:8000/api/v1   → ws://host:8000/api/v1/…
 * Caddy routes /api/* → backend, so this hits the FastAPI WS endpoint directly.
 *
 * The JWT must travel in the query string — browsers can't set WS auth headers
 * (Contract #2) — and is percent-encoded so a token's special chars survive.
 *
 * `initialSeek` is the footage epoch (UTC seconds) to start playback from.
 * The backend requires `?t=<epoch>` before accept() and closes 4004 if missing.
 */
export function buildPlaybackWsUrl(
  nvrId: string,
  channel: number,
  token: string,
  initialSeek: number,
): string {
  const wsBase = httpToWsBase(CONFIG.backendBase);
  return `${wsBase}/playback/${nvrId}/${channel}/stream?token=${encodeURIComponent(token)}&t=${initialSeek}`;
}

// ── Footage-time mapping ───────────────────────────────────────────────────────

/**
 * Compute the current footage epoch from the MSE anchor.
 *
 * Speed is applied server-side (frame decimation); the <video> element always
 * plays at playbackRate=1.0, so currentTime advances at wall-clock rate.
 * The footage epoch advances at speed × wall-clock rate — but because the server
 * controls the stream, video.currentTime already reflects that rate directly.
 * (anchor.speed is stored for reference / future use, not used in the formula.)
 */
export function footageEpoch(anchor: FootageAnchor, currentTime: number): number {
  return anchor.t0 + (currentTime - anchor.baseCt);
}

// ── Clip lookup ───────────────────────────────────────────────────────────────

/**
 * Return the clip that contains the given epoch, or null if the epoch falls in a
 * gap (or outside all clips entirely).
 *
 * Clips are expected to be sorted by start_epoch ascending and non-overlapping.
 * The start boundary is inclusive; end_epoch is exclusive.
 */
export function findClipAt(clips: RecordingClip[], epoch: number): RecordingClip | null {
  for (const clip of clips) {
    if (epoch >= clip.start_epoch && epoch < clip.end_epoch) return clip;
  }
  return null;
}

/**
 * Snap a requested seek epoch to the nearest covered time.
 *
 * - Epoch inside a clip → return epoch unchanged.
 * - Epoch in a gap between clips → snap forward to the start of the next clip.
 * - Epoch before the first clip → return first clip's start.
 * - Epoch at or after the end of the last clip (or clips is empty) → return null
 *   (caller should transition to "end" / "no_coverage" state).
 */
export function snapToNearest(clips: RecordingClip[], epoch: number): number | null {
  if (clips.length === 0) return null;

  // Before the first clip
  if (epoch < clips[0].start_epoch) return clips[0].start_epoch;

  for (const clip of clips) {
    // Inside this clip
    if (epoch >= clip.start_epoch && epoch < clip.end_epoch) return epoch;
    // In a gap before the next clip — snap to next clip start
    // We'll find the next clip in the next iteration by checking if epoch < next.start
  }

  // Find the next clip after the epoch (handles gap case)
  for (let i = 0; i < clips.length; i++) {
    if (epoch < clips[i].start_epoch) return clips[i].start_epoch;
  }

  // Epoch is at or past the end of the last clip
  return null;
}

// ── Calendar / timezone utilities ─────────────────────────────────────────────

/**
 * Return the day boundaries as UTC epoch seconds for a YYYY-MM-DD string in the
 * NVR's timezone.  Mirrors backend day_to_epochs().
 *
 * tz_offset_minutes: NVR local = UTC + tz_offset_minutes (same sign convention as backend).
 *
 * IMPORTANT: This is a pure calendar conversion — it does NOT handle DST transitions
 * within the NVR's tz (the NVR uses a fixed UTC offset, not a DST-aware zone).
 * The returned span is always exactly 86 400 s.
 */
export function dayToEpochs(
  dateStr: string,           // "YYYY-MM-DD"
  tzOffsetMinutes: number,   // from RecordingIndex.tz_offset_minutes
): [dayStart: number, dayEnd: number] {
  const [year, month, day] = dateStr.split("-").map(Number);
  // NVR midnight 00:00:00 local = UTC midnight − offset
  const dayStartUtcMs = Date.UTC(year, month - 1, day, 0, 0, 0) - tzOffsetMinutes * 60 * 1000;
  const dayStart = dayStartUtcMs / 1000;
  return [dayStart, dayStart + 86400];
}

// ── Time string formatters ─────────────────────────────────────────────────────

function pad2(n: number): string {
  return String(Math.floor(n)).padStart(2, "0");
}

/**
 * Format a UTC epoch as HH:MM:SS in NVR-local time.
 * Useful for timeline axis labels.
 */
export function epochToNvrTimeStr(epoch: number, tzOffsetMinutes: number): string {
  // Convert to NVR-local epoch (in seconds)
  const localEpoch = epoch + tzOffsetMinutes * 60;
  // Extract time of day in seconds
  const todSeconds = ((localEpoch % 86400) + 86400) % 86400;
  const hh = Math.floor(todSeconds / 3600);
  const mm = Math.floor((todSeconds % 3600) / 60);
  const ss = Math.floor(todSeconds % 60);
  return `${pad2(hh)}:${pad2(mm)}:${pad2(ss)}`;
}

/**
 * Format a UTC epoch as "YYYY-MM-DD HH:MM:SS" in NVR-local time.
 * Used for display labels, not for NVR API calls (see epochToNvrTimeStr for time-only).
 * For snapshot filenames use buildSnapshotFilename (which uses the filesystem-safe variant).
 */
export function formatNvrDatetime(epoch: number, tzOffsetMinutes: number): string {
  const localEpoch = epoch + tzOffsetMinutes * 60;
  // Days since Unix epoch
  const totalDays = Math.floor(localEpoch / 86400);
  const todSeconds = ((localEpoch % 86400) + 86400) % 86400;

  // Convert day count to calendar date (proleptic Gregorian)
  // Algorithm: civil date from day number (Gregorian)
  const z = totalDays + 719468;
  const era = Math.floor((z >= 0 ? z : z - 146096) / 146097);
  const doe = z - era * 146097;
  const yoe = Math.floor((doe - Math.floor(doe / 1460) + Math.floor(doe / 36524) - Math.floor(doe / 146096)) / 365);
  const y = yoe + era * 400;
  const doy = doe - (365 * yoe + Math.floor(yoe / 4) - Math.floor(yoe / 100));
  const mp = Math.floor((5 * doy + 2) / 153);
  const d = doy - Math.floor((153 * mp + 2) / 5) + 1;
  const m = mp < 10 ? mp + 3 : mp - 9;
  const yr = m <= 2 ? y + 1 : y;

  const hh = Math.floor(todSeconds / 3600);
  const mm = Math.floor((todSeconds % 3600) / 60);
  const ss = Math.floor(todSeconds % 60);

  return `${yr}-${pad2(m)}-${pad2(d)} ${pad2(hh)}:${pad2(mm)}:${pad2(ss)}`;
}

// ── Snapshot filename builder ──────────────────────────────────────────────────

/**
 * Build a filesystem-safe snapshot filename:
 *   "snapshot_{sanitized-camName}_{NVR-local-datetime}.png"
 *
 * The datetime component uses hyphens and an underscore separator
 * ("YYYY-MM-DD_HH-MM-SS") so the filename is safe on all platforms.
 * camName is sanitized: any character that is not ASCII alphanumeric is
 * replaced with "_".
 *
 * Example:  buildSnapshotFilename(1751241600, 0, "Front Gate")
 *           → "snapshot_Front_Gate_2025-06-30_00-00-00.png"
 */
export function buildSnapshotFilename(
  epoch: number,
  tzOffsetMinutes: number,
  camName: string,
): string {
  // Reuse formatNvrDatetime ("YYYY-MM-DD HH:MM:SS") and convert to
  // filesystem-safe form: space → "_", ":" → "-".
  const dtStr = formatNvrDatetime(epoch, tzOffsetMinutes)
    .replace(" ", "_")
    .replace(/:/g, "-");
  const sanitizedCam = camName.replace(/[^a-z0-9]/gi, "_");
  return `snapshot_${sanitizedCam}_${dtStr}.png`;
}
