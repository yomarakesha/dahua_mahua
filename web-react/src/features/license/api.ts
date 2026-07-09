/**
 * License feature API — self-contained (deliberately NOT in @/api/hooks.ts).
 *
 * GET  /license is public (no token needed) so the activation screen renders on
 * a fresh install; POST /license requires an admin token (handled by the shared
 * fetch wrapper adding the Bearer header).
 */
import { http } from "@/api/client";

export interface LicenseLimits {
  max_cameras: number | null;
  max_nvrs: number | null;
}

/** Enforcement state machine (mirrors backend licensing.license_state). */
export type LicenseState =
  | "valid"
  | "grace"
  | "expired"
  | "missing"
  | "invalid"
  | "mismatch";

/** States in which the backend hard-blocks the protected API when enforced. */
export const BLOCKED_STATES: readonly LicenseState[] = [
  "expired",
  "missing",
  "invalid",
  "mismatch",
];

export function isBlockedState(state: LicenseState | undefined): boolean {
  return !!state && BLOCKED_STATES.includes(state);
}

export interface LicenseStatus {
  valid: boolean;
  reason: string;
  customer: string | null;
  site_id: string | null;
  issued: string | null;
  expires: string | null;
  features: string[];
  limits: LicenseLimits;
  days_left: number | null;
  fingerprint: string;
  // Enforcement view (added by the backend GET /license). `enforced` is the
  // deployment flag; `state` is the computed state; grace_days_left is only
  // meaningful in the "grace" state.
  state: LicenseState;
  enforced: boolean;
  grace_days_left: number | null;
}

export function fetchLicense(): Promise<LicenseStatus> {
  return http.get<LicenseStatus>("/license");
}

export function uploadLicense(licenseText: string): Promise<LicenseStatus> {
  // Raw text body — the backend reads it as text/plain and verifies the JSON.
  return http.post<LicenseStatus>("/license", licenseText);
}
