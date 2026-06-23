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

/** go2rtc stream name for a camera tile. sub = `{nvr}_ch{N}`, main = `…_main`. */
export function streamName(cam: Pick<Camera, "nvr_id" | "channel">, quality: StreamQuality): string {
  return quality === "main" ? `${cam.nvr_id}_ch${cam.channel}_main` : `${cam.nvr_id}_ch${cam.channel}`;
}
