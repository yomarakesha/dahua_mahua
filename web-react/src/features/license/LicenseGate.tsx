/**
 * LicenseGate — app-root license enforcement.
 *
 * Fetches GET /license once on mount and decides what the whole app renders:
 *   • not enforced (the shipped default) or valid  → the app, untouched.
 *   • enforced + a BLOCKED state (expired-past-grace / missing / invalid /
 *     mismatch) → a full-screen block instead of the app: it explains the state,
 *     shows this machine's fingerprint (copy), and — for an admin — an inline
 *     .lic activation so they can recover without a shell. Non-admins get a
 *     "contact your administrator" notice + a log-in affordance.
 *   • enforced + grace → the app plus a dismissible renewal-warning banner.
 *
 * FAIL-OPEN: while the status is loading, or if the fetch fails, the app renders
 * normally — enforcement never blocks on a transient error, and the live deploy
 * (enforcement OFF) is completely unaffected. A 402 that slips through an API
 * call re-drives the block via a window event, so a server-side block can't be
 * bypassed by a stale client.
 */
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { KeyIcon, CheckIcon, XIcon } from "@/components/icons";
import { useAuth } from "@/lib/auth";
import { ApiError } from "@/api/client";
import {
  fetchLicense,
  uploadLicense,
  isBlockedState,
  type LicenseState,
  type LicenseStatus,
} from "./api";

/** Copyable machine fingerprint block. */
function Fingerprint({ fingerprint }: { fingerprint: string }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const copy = useCallback(() => {
    if (!fingerprint) return;
    navigator.clipboard?.writeText(fingerprint).then(
      () => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      },
      () => setCopied(false),
    );
  }, [fingerprint]);
  return (
    <div className="w-full">
      <div className="dss-label mb-2 tracking-[1.4px]">{t("license.machineFingerprint")}</div>
      <div className="flex flex-wrap items-center gap-3">
        <code className="flex-1 break-all rounded-md border border-white/[.08] bg-black/30 px-3 py-2 font-mono text-sm text-ink-soft">
          {fingerprint || "—"}
        </code>
        <button type="button" className="dss-btn-ghost h-[38px] px-4" onClick={copy} disabled={!fingerprint}>
          {copied ? <CheckIcon size={14} /> : null}
          {copied ? t("license.copied") : t("license.copy")}
        </button>
      </div>
    </div>
  );
}

/** Inline .lic activation for admins on the block screen. */
function InlineActivate({ onActivated }: { onActivated: (s: LicenseStatus) => void }) {
  const { t } = useTranslation();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function activate() {
    if (busy || !text.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const status = await uploadLicense(text);
      if (status.valid || status.state === "grace") {
        onActivated(status);
      } else {
        setError(status.reason || t("license.licenseNotValid"));
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.message : t("license.errUploadFailed"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="w-full">
      <div className="dss-label mb-2 flex items-center gap-2 tracking-[1.4px]">
        <KeyIcon size={14} className="text-accent-light" />
        {t("license.activateTitle")}
      </div>
      <textarea
        className="dss-input min-h-[110px] w-full resize-y font-mono text-xs"
        placeholder='{ "customer": "...", "sig": "..." }'
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <label className="dss-btn-ghost h-[38px] cursor-pointer px-4">
          {t("license.chooseLicFile")}
          <input
            type="file"
            accept=".lic,.json,application/json,text/plain"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) void f.text().then(setText).catch(() => setError(t("license.errReadFile")));
              e.target.value = "";
            }}
          />
        </label>
        <button
          type="button"
          className="dss-btn-primary h-[38px] px-6"
          onClick={() => void activate()}
          disabled={busy || text.trim() === ""}
        >
          {busy ? t("license.activating") : t("license.activate")}
        </button>
        {error && (
          <span className="flex items-center gap-1.5 text-sm text-danger">
            <XIcon size={14} /> {error}
          </span>
        )}
      </div>
    </div>
  );
}

/** Full-screen block shown when enforced + a blocked license state. */
function BlockScreen({
  state,
  fingerprint,
  onRecovered,
}: {
  state: LicenseState;
  fingerprint: string;
  onRecovered: (s: LicenseStatus) => void;
}) {
  const { t } = useTranslation();
  const { isAdmin, me } = useAuth();
  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center overflow-y-auto bg-bg p-5">
      <div className="dss-panel flex w-full max-w-[600px] flex-col gap-5 p-6 sm:p-8">
        <div className="flex items-center gap-3">
          <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg border border-danger/25 bg-danger/[.10] text-danger">
            <XIcon size={20} />
          </div>
          <div>
            <h1 className="text-[18px] font-extrabold text-ink-bright">{t("licenseGate.blockedTitle")}</h1>
            <p className="text-sm text-ink-dim">{t(`licenseGate.state.${state}`)}</p>
          </div>
        </div>

        <Fingerprint fingerprint={fingerprint} />

        {isAdmin ? (
          <InlineActivate onActivated={onRecovered} />
        ) : (
          <div className="flex flex-col gap-3">
            <p className="text-sm text-ink-dim">{t("licenseGate.contactAdmin")}</p>
            {!me && (
              <a href="#/login" className="dss-btn-ghost inline-flex h-[38px] w-fit items-center px-4">
                {t("licenseGate.loginAsAdmin")}
              </a>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

/** Dismissible renewal warning shown above the app during the grace period. */
function GraceBanner({ daysLeft, onDismiss }: { daysLeft: number | null; onDismiss: () => void }) {
  const { t } = useTranslation();
  const n = daysLeft == null ? 0 : Math.max(0, daysLeft);
  return (
    <div className="flex items-center justify-between gap-3 border-b border-warn/25 bg-warn/[.10] px-4 py-2 text-sm text-warn">
      <span className="font-medium">{t("licenseGate.graceWarning", { days: n })}</span>
      <button type="button" className="shrink-0 rounded p-1 hover:bg-white/[.06]" onClick={onDismiss} aria-label={t("common.dismiss")}>
        <XIcon size={14} />
      </button>
    </div>
  );
}

export function LicenseGate({ children }: { children: ReactNode }) {
  const loc = useLocation();
  const [status, setStatus] = useState<LicenseStatus | null>(null);
  const [loaded, setLoaded] = useState(false);
  // A 402 from any API call forces the block even if our cached status was stale.
  const [forcedState, setForcedState] = useState<LicenseState | null>(null);
  const [graceDismissed, setGraceDismissed] = useState(false);

  const load = useCallback(async () => {
    try {
      setStatus(await fetchLicense());
    } catch {
      setStatus(null); // fail-open — never block on a fetch error
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    function onBlocked(e: Event) {
      const detail = (e as CustomEvent<{ state?: LicenseState }>).detail;
      setForcedState(detail?.state ?? "invalid");
    }
    window.addEventListener("license-blocked", onBlocked);
    return () => window.removeEventListener("license-blocked", onBlocked);
  }, []);

  const onRecovered = useCallback((s: LicenseStatus) => {
    setForcedState(null);
    setStatus(s);
  }, []);

  const fingerprint = status?.fingerprint ?? "";
  const enforced = forcedState !== null || !!status?.enforced;
  const state: LicenseState | undefined = forcedState ?? status?.state;

  // Always let the login route render so an admin can authenticate to recover.
  const onLogin = loc.pathname.startsWith("/login");

  // Fail-open while first load is in flight (unless a 402 already forced a block).
  if (!loaded && forcedState === null) return <>{children}</>;

  if (enforced && !onLogin && isBlockedState(state)) {
    return <BlockScreen state={state as LicenseState} fingerprint={fingerprint} onRecovered={onRecovered} />;
  }

  const showGrace = enforced && state === "grace" && !graceDismissed;
  return (
    <>
      {showGrace && (
        <GraceBanner daysLeft={status?.grace_days_left ?? null} onDismiss={() => setGraceDismissed(true)} />
      )}
      {children}
    </>
  );
}

export default LicenseGate;
