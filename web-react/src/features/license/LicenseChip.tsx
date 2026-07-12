import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { KeyIcon } from "@/components/icons";
import { fetchLicense } from "./api";

/**
 * Compact license-status chip for the top bar (replaces the old License nav tab).
 * Shows the real state at a glance and opens the License screen on click.
 *
 * Tone rules — deliberately NOT alarming when enforcement is off:
 *  - valid            → green "Licensed" (amber "Expires Nd" when ≤30 days left)
 *  - grace            → amber "Grace Nd"
 *  - blocked states   → red when `enforced` (actionable), else muted grey
 *                       (informational — the app isn't gated, so no false alarm)
 * Admin-only (rendered by AppShell); the License page is admin-only too.
 */
export function LicenseChip() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { data } = useQuery({
    queryKey: ["license-status"],
    queryFn: fetchLicense,
    staleTime: 60_000,
    refetchInterval: 300_000,
    retry: false,
  });
  if (!data) return null;

  const { state, days_left, grace_days_left, enforced } = data;

  let tone: "ok" | "warn" | "block" | "muted";
  let label: string;
  if (state === "valid") {
    if (days_left != null && days_left <= 30) {
      tone = "warn";
      label = t("license.chip.expiresIn", { days: days_left });
    } else {
      tone = "ok";
      label = t("license.chip.licensed");
    }
  } else if (state === "grace") {
    tone = "warn";
    label = t("license.chip.grace", { days: grace_days_left ?? 0 });
  } else {
    // expired | missing | invalid | mismatch
    label = t(`license.chip.${state}`);
    tone = enforced ? "block" : "muted";
  }

  const toneCls = {
    ok: "bg-accent/[.14] text-accent-light ring-accent/30",
    warn: "bg-amber-400/[.14] text-amber-300 ring-amber-400/30",
    block: "bg-danger/[.14] text-danger ring-danger/40",
    muted: "bg-white/[.05] text-ink-dim ring-white/10 hover:text-ink-soft",
  }[tone];

  return (
    <button
      type="button"
      onClick={() => navigate("/license")}
      title={t("license.chip.title")}
      aria-label={t("license.chip.title")}
      className={`flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-xs font-semibold ring-1 transition ${toneCls}`}
    >
      <KeyIcon size={13} />
      <span className="whitespace-nowrap">{label}</span>
    </button>
  );
}
