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
}

export function fetchLicense(): Promise<LicenseStatus> {
  return http.get<LicenseStatus>("/license");
}

export function uploadLicense(licenseText: string): Promise<LicenseStatus> {
  // Raw text body — the backend reads it as text/plain and verifies the JSON.
  return http.post<LicenseStatus>("/license", licenseText);
}
