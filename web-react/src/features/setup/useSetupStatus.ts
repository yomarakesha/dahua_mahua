/**
 * First-run setup status.
 *
 * The system is "fresh" when the signed-in admin still holds the bootstrap
 * password (must_change_password) OR no NVRs have been added yet. In either
 * case we route the admin through /setup instead of the normal app. A normal
 * operator never triggers setup (it needs admin) and never sees the wizard.
 *
 * The dismiss flag is session-scoped: once the admin finishes or skips the
 * wizard we stop auto-redirecting for the rest of the tab session, but the
 * admin can still reach /setup manually later.
 */
import { useNvrs } from "@/api/hooks";
import { useAuth } from "@/lib/auth";

const DISMISS_KEY = "kanagatly.setupDismissed";

/** Stop auto-redirecting to /setup for the rest of this tab session. */
export function dismissSetup(): void {
  try {
    sessionStorage.setItem(DISMISS_KEY, "1");
  } catch {
    /* sessionStorage unavailable — degrade to always-eligible; harmless. */
  }
}

export function isSetupDismissed(): boolean {
  try {
    return sessionStorage.getItem(DISMISS_KEY) === "1";
  } catch {
    return false;
  }
}

export interface SetupStatus {
  /** Admin still on the bootstrap password. */
  needsPasswordChange: boolean;
  /** Admin, and no NVRs exist yet (query resolved). */
  needsFirstNvr: boolean;
  /** Either condition holds → the wizard is warranted. */
  needed: boolean;
  /** NVR list still loading — callers should hold redirects until settled. */
  loading: boolean;
}

export function useSetupStatus(): SetupStatus {
  const { me, isAdmin } = useAuth();
  const nvrs = useNvrs();
  const needsPasswordChange = isAdmin && !!me?.must_change_password;
  const needsFirstNvr = isAdmin && nvrs.isSuccess && (nvrs.data?.length ?? 0) === 0;
  return {
    needsPasswordChange,
    needsFirstNvr,
    needed: needsPasswordChange || needsFirstNvr,
    loading: nvrs.isLoading,
  };
}
