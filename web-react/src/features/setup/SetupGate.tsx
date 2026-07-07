import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";
import { useAuth } from "@/lib/auth";
import { isSetupDismissed, useSetupStatus } from "./useSetupStatus";

/**
 * Wraps the app's landing route: when a fresh system is detected (admin on the
 * bootstrap password, or zero NVRs) and setup hasn't been dismissed this
 * session, bounce the admin to the /setup wizard. Otherwise render the app.
 * Operators fall straight through.
 */
export function SetupGate({ children }: { children: ReactNode }) {
  const { isAdmin } = useAuth();
  const status = useSetupStatus();
  if (isAdmin && !isSetupDismissed() && !status.loading && status.needed) {
    return <Navigate to="/setup" replace />;
  }
  return <>{children}</>;
}
