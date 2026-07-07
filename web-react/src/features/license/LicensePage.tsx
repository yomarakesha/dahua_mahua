import { useState, useEffect, useCallback } from "react";
import { KeyIcon, CheckIcon, XIcon, RefreshIcon } from "@/components/icons";
import { useAuth } from "@/lib/auth";
import { ApiError } from "@/api/client";
import { fetchLicense, uploadLicense, type LicenseStatus } from "./api";

type StateKind = "valid" | "expired" | "invalid" | "none";

function classify(s: LicenseStatus | null): StateKind {
  if (!s) return "none";
  if (s.valid) return "valid";
  const reason = (s.reason || "").toLowerCase();
  if (reason.includes("expired")) return "expired";
  if (reason.includes("no license")) return "none";
  return "invalid";
}

const BADGE: Record<StateKind, { label: string; cls: string }> = {
  valid: { label: "Licensed", cls: "border-accent/25 bg-accent/[.12] text-accent-light" },
  expired: { label: "Expired", cls: "border-warn/25 bg-warn/[.10] text-warn" },
  invalid: { label: "Invalid", cls: "border-danger/25 bg-danger/[.10] text-danger" },
  none: { label: "Unlicensed", cls: "border-white/[.08] bg-white/[.04] text-ink-mute" },
};

/** Labelled read-only value column. */
function Info({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="dss-label">{label}</span>
      <span className="text-sm text-ink-bright">{value}</span>
    </div>
  );
}

function FingerprintPanel({ fingerprint }: { fingerprint: string }) {
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
    <section className="dss-panel p-5">
      <div className="dss-label mb-3 tracking-[1.4px]">Machine fingerprint</div>
      <p className="mb-3 text-sm text-ink-dim">
        Send this code to your vendor to obtain or renew a license. It is unique to this machine.
      </p>
      <div className="flex flex-wrap items-center gap-3">
        <code className="flex-1 break-all rounded-md border border-white/[.08] bg-black/30 px-3 py-2 font-mono text-sm text-ink-soft">
          {fingerprint || "—"}
        </code>
        <button
          type="button"
          className="dss-btn-ghost h-[38px] px-4"
          onClick={copy}
          disabled={!fingerprint}
        >
          {copied ? <CheckIcon size={14} /> : null}
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
    </section>
  );
}

function StatusPanel({ status }: { status: LicenseStatus | null }) {
  const kind = classify(status);
  const badge = BADGE[kind];
  return (
    <section className="dss-panel p-5">
      <div className="mb-4 flex items-center justify-between gap-3">
        <span className="dss-label tracking-[1.4px]">License status</span>
        <span
          className={[
            "inline-flex items-center rounded-md border px-2.5 py-0.5 text-2xs font-bold uppercase tracking-wider",
            badge.cls,
          ].join(" ")}
        >
          {badge.label}
        </span>
      </div>

      {status && status.reason && kind !== "valid" && (
        <div
          className={[
            "mb-4 rounded-md px-3 py-2 text-sm font-medium",
            kind === "expired"
              ? "border border-warn/25 bg-warn/[.10] text-warn"
              : kind === "invalid"
                ? "border border-danger/25 bg-danger/[.10] text-danger"
                : "border border-white/[.08] bg-white/[.04] text-ink-mute",
          ].join(" ")}
        >
          {status.reason}
        </div>
      )}

      {status && (status.customer || status.valid || kind === "expired") ? (
        <div className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3">
          <Info label="Customer" value={status.customer || "—"} />
          <Info label="Site" value={status.site_id || "—"} />
          <Info label="Issued" value={status.issued || "—"} />
          <Info label="Expires" value={status.expires || "Perpetual"} />
          <Info
            label="Days left"
            value={
              status.days_left === null || status.days_left === undefined
                ? status.expires
                  ? "—"
                  : "∞"
                : status.days_left
            }
          />
          <Info
            label="Limits"
            value={`${status.limits?.max_cameras ?? "—"} cams · ${status.limits?.max_nvrs ?? "—"} NVRs`}
          />
          <div className="col-span-2 flex flex-col gap-1 sm:col-span-3">
            <span className="dss-label">Features</span>
            <div className="flex flex-wrap gap-1.5">
              {status.features && status.features.length > 0 ? (
                status.features.map((f) => (
                  <span
                    key={f}
                    className="rounded-md border border-white/[.08] bg-white/[.04] px-2 py-0.5 text-2xs font-semibold uppercase tracking-wider text-ink-soft"
                  >
                    {f}
                  </span>
                ))
              ) : (
                <span className="text-sm text-ink-dim">—</span>
              )}
            </div>
          </div>
        </div>
      ) : (
        <p className="text-sm text-ink-dim">No license is installed on this machine.</p>
      )}
    </section>
  );
}

function ActivatePanel({ onActivated }: { onActivated: (s: LicenseStatus) => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);

  async function readFile(file: File) {
    setError(null);
    setOk(null);
    try {
      const content = await file.text();
      setText(content);
    } catch {
      setError("Could not read the selected file.");
    }
  }

  async function activate() {
    if (busy || !text.trim()) return;
    setBusy(true);
    setError(null);
    setOk(null);
    try {
      const status = await uploadLicense(text);
      onActivated(status);
      if (status.valid) {
        setOk("License activated.");
        setText("");
      } else {
        setError(status.reason || "License is not valid.");
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to upload license.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="dss-panel p-5">
      <div className="mb-4 flex items-center gap-2">
        <KeyIcon size={15} className="text-accent-light" />
        <span className="dss-label tracking-[1.4px]">Activate license</span>
      </div>
      <p className="mb-3 text-sm text-ink-dim">
        Paste the contents of your <code className="font-mono text-ink-soft">.lic</code> file, or
        upload it below.
      </p>
      <textarea
        className="dss-input min-h-[140px] w-full resize-y font-mono text-xs"
        placeholder='{ "customer": "...", "sig": "..." }'
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <label className="dss-btn-ghost h-[38px] cursor-pointer px-4">
          Choose .lic file
          <input
            type="file"
            accept=".lic,.json,application/json,text/plain"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) void readFile(f);
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
          {busy ? "Activating…" : "Activate"}
        </button>
        {error && (
          <span className="flex items-center gap-1.5 text-sm text-danger">
            <XIcon size={14} /> {error}
          </span>
        )}
        {ok && (
          <span className="flex items-center gap-1.5 text-sm text-accent">
            <CheckIcon size={14} /> {ok}
          </span>
        )}
      </div>
    </section>
  );
}

export default function LicensePage() {
  const { isAdmin } = useAuth();
  const [status, setStatus] = useState<LicenseStatus | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setStatus(await fetchLicense());
    } catch {
      setStatus(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const fingerprint = status?.fingerprint ?? "";

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex max-w-[760px] flex-col gap-4 p-5 lg:p-7">
        <header className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg border border-accent/25 bg-accent/[.12] text-accent-light">
              <KeyIcon size={17} />
            </div>
            <div>
              <h1 className="text-[17px] font-extrabold text-ink-bright">License</h1>
              <p className="text-sm text-ink-dim">Activation, entitlements and machine binding</p>
            </div>
          </div>
          <button
            type="button"
            className="dss-btn-ghost h-[38px] px-4"
            onClick={() => void refresh()}
            disabled={loading}
          >
            <RefreshIcon size={14} />
            Refresh
          </button>
        </header>

        <StatusPanel status={status} />
        <FingerprintPanel fingerprint={fingerprint} />
        {isAdmin ? (
          <ActivatePanel onActivated={setStatus} />
        ) : (
          <p className="text-sm text-ink-dim">
            Only an administrator can install or update the license file.
          </p>
        )}
      </div>
    </div>
  );
}
