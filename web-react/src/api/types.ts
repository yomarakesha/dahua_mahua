/** Types mirroring backend/app/schemas.py (kept in sync by hand). */

export type Role = "admin" | "operator";
export type Vendor = "dahua" | "hikvision";
export type StreamQuality = "sub" | "main";

export interface Me {
  id: string;
  username: string;
  role: Role;
  is_active: boolean;
  must_change_password: boolean;
  created_at: string;
  last_login_at: string | null;
  region_ids: string[];
  camera_ids: string[];
}

/** Full user record (admin user-management). Same shape as Me. */
export type User = Me;

export interface UserCreate {
  username: string;
  password: string;
  role: Role;
  is_active?: boolean;
  camera_ids?: string[];
}

export interface UserUpdate {
  role?: Role;
  is_active?: boolean;
  new_password?: string;
  camera_ids?: string[];
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  must_change_password: boolean;
}

export interface Region {
  id: string;
  slug: string;
  name: string;
  created_at: string;
}

export interface Nvr {
  id: string;
  label: string;
  ip: string;
  port: number;
  rtsp_username: string;
  vendor: Vendor;
  enabled: boolean;
  group: string | null;
  region_id: string | null;
  created_at: string;
  updated_at: string;
  camera_count: number;
  create_notice: string | null;
}

export interface NvrCreate {
  id?: string | null;
  label: string;
  ip: string;
  port?: number;
  rtsp_username?: string;
  rtsp_password: string;
  vendor?: Vendor;
  enabled?: boolean;
  group?: string | null;
  region_id?: string | null;
  channels?: number | null;
  skip_probe?: boolean;
}

export interface NvrUpdate {
  label?: string;
  ip?: string;
  port?: number;
  rtsp_username?: string;
  rtsp_password?: string;
  vendor?: Vendor;
  enabled?: boolean;
  group?: string | null;
  region_id?: string | null;
}

export interface Camera {
  id: string;
  nvr_id: string;
  channel: number;
  name: string | null;
  ip: string | null;
  enabled: boolean;
  has_sub: boolean;
  has_main: boolean;
  display_name: string;
  region_id: string | null;
}

export interface CameraCreate {
  nvr_id: string;
  channel: number;
  name?: string | null;
  enabled?: boolean;
  has_sub?: boolean;
  has_main?: boolean;
}

export interface CameraUpdate {
  name?: string | null;
  ip?: string | null;
  enabled?: boolean;
  has_sub?: boolean;
  has_main?: boolean;
}

export interface NvrHealthResult {
  nvr_id: string;
  ok: boolean;
  message: string;
}

export interface NvrTestResult {
  ok: boolean;
  message: string;
  banned_until: number | null;
  remaining: number | null;
}

export interface NvrEvent {
  id: string;
  nvr_id: string;
  ip: string;
  event_type: string;
  message: string | null;
  created_at: string;
}

export interface CameraIpImportResult {
  nvr_id: string;
  found: number;
  updated: number;
  message: string;
}

// ── Playback recording types ──────────────────────────────────────────────────

/** One merged clip span from GET /playback/{nvr_id}/{ch}/index */
export interface RecordingClip {
  start_epoch: number;   // UTC epoch seconds (inclusive)
  end_epoch: number;     // UTC epoch seconds (exclusive)
  type: string;          // e.g. "dav" (container type from NVR)
  stream: string;        // "Main" (always Main per spike V4)
}

/** Full response from GET /playback/{nvr_id}/{ch}/index?date=YYYY-MM-DD */
export interface RecordingIndex {
  tz_offset_minutes: number;   // NVR local = UTC + tz_offset_minutes
  day_start_epoch: number;     // epoch of 00:00:00 NVR-local
  day_end_epoch: number;       // epoch of 00:00:00 NVR-local next day
  clips: RecordingClip[];
}

/** Response from GET /playback/{nvr_id}/{ch}/availability?month=YYYY-MM */
export interface RecordingAvailability {
  days_with_recordings: string[];  // sorted ["YYYY-MM-DD", ...]
  oldest_epoch: number | null;     // epoch of oldest clip start, null if empty month
}

/**
 * go2rtc stream name for a camera. sub = `{nvr}_ch{N}`, main = `…_main` (direct
 * from the camera IP). `viaNvr` selects the relay variant `…_main_nvr` (pulled
 * through the NVR) — used by the fullscreen source toggle. Sub has no via-NVR
 * variant (the grid stays direct-from-camera to avoid overloading the NVR).
 */
export function streamName(
  cam: Pick<Camera, "nvr_id" | "channel">,
  quality: StreamQuality,
  viaNvr = false,
): string {
  if (quality === "main") {
    return viaNvr
      ? `${cam.nvr_id}_ch${cam.channel}_main_nvr`
      : `${cam.nvr_id}_ch${cam.channel}_main`;
  }
  return `${cam.nvr_id}_ch${cam.channel}`;
}
