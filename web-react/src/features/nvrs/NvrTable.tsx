import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { useDeleteNvr, useTestNvr, useUpdateNvr } from "@/api/hooks";
import type { Nvr, NvrHealthResult, NvrTestResult, Vendor } from "@/api/types";
import { VENDORS } from "@/api/types";
import { CameraIcon, CheckIcon, PencilIcon, PlayIcon, TrashIcon, XIcon } from "@/components/icons";

const GRID = "grid-cols-[40px_1.3fr_1.2fr_1.3fr_.6fr_1fr_1fr_1.6fr]";

interface Props {
  nvrs: Nvr[];
  showHealth: boolean;
  health: Record<string, NvrHealthResult>;
}

/** NVR list table. Per-row: health dot, channel count, Test, Cams, edit, delete. */
export function NvrTable({ nvrs, showHealth, health }: Props) {
  const { t } = useTranslation();
  if (nvrs.length === 0) {
    return (
      <div className="rounded-xl border border-white/[.06] bg-deep/60 px-4 py-10 text-center text-sm text-ink-dim">
        {t("nvrs.noRecorders")}
      </div>
    );
  }
  return (
    // Horizontal scroll below the table's min width so the 8 columns + 5 action
    // controls never crush/overflow on narrow viewports.
    <div className="overflow-x-auto">
      <div className="min-w-[900px]">
        <div
          className={`grid ${GRID} gap-2.5 px-3.5 pb-2.5 text-2xs font-extrabold uppercase tracking-wider text-ink-faint`}
        >
          <span>{showHealth ? t("nvrs.ok") : t("nvrs.on")}</span>
          <span>{t("nvrs.id")}</span>
          <span>{t("nvrs.label")}</span>
          <span>{t("nvrs.ip")}</span>
          <span>{t("nvrs.port")}</span>
          <span>{t("nvrs.user")}</span>
          <span>{t("nvrs.vendor")}</span>
          <span>{t("common.actions")}</span>
        </div>
        <div className="space-y-2">
          {nvrs.map((n) => (
            <NvrRow key={n.id} nvr={n} showHealth={showHealth} health={health[n.id]} />
          ))}
        </div>
      </div>
    </div>
  );
}

function NvrRow({
  nvr,
  showHealth,
  health,
}: {
  nvr: Nvr;
  showHealth: boolean;
  health?: NvrHealthResult;
}) {
  const { t } = useTranslation();
  const test = useTestNvr();
  const update = useUpdateNvr();
  const del = useDeleteNvr();
  const [editing, setEditing] = useState(false);
  const [confirmDel, setConfirmDel] = useState(false);
  const [testResult, setTestResult] = useState<NvrTestResult | null>(null);
  // Set when the vendor was just changed inline — surfaces the recovery hint
  // (change vendor → auto re-Test → enable) while the NVR is still disabled.
  const [vendorChanged, setVendorChanged] = useState(false);
  // Optimistic vendor for the select: shows the operator's choice immediately
  // instead of snapping back to the old value during the PATCH+refetch round-trip.
  const [pendingVendor, setPendingVendor] = useState<Vendor | null>(null);
  // Clear the optimistic value once the server state has caught up.
  useEffect(() => {
    if (pendingVendor && nvr.vendor === pendingVendor) setPendingVendor(null);
  }, [nvr.vendor, pendingVendor]);
  // Once the NVR is enabled the recovery hint has served its purpose — drop it.
  useEffect(() => {
    if (nvr.enabled) setVendorChanged(false);
  }, [nvr.enabled]);

  function runTest() {
    test.mutate(nvr.id, {
      onSuccess: (r) => setTestResult(r),
      onError: (e) => setTestResult({ ok: false, message: (e as Error).message, banned_until: null, remaining: null }),
    });
  }

  // Changing vendor rewrites the RTSP path template (Dahua vs Hikvision), so we
  // immediately re-validate: PATCH the vendor, then auto-run Test on success so
  // the operator sees whether the new vendor's path works before enabling.
  function changeVendor(vendor: Vendor) {
    if (vendor === nvr.vendor || update.isPending) return;
    setPendingVendor(vendor);
    setVendorChanged(true);
    update.mutate(
      { id: nvr.id, body: { vendor } },
      { onSuccess: () => runTest(), onError: () => setPendingVendor(null) },
    );
  }

  return (
    <div className="rounded-xl border border-white/[.06] bg-deep/60">
      <div className={`grid ${GRID} items-center gap-2.5 px-3.5 py-3`}>
        {/* health dot / enabled toggle */}
        {showHealth ? (
          <Dot ok={health?.ok} title={health?.message} />
        ) : (
          <button
            type="button"
            role="switch"
            aria-checked={nvr.enabled}
            aria-label={nvr.enabled ? t("nvrs.enabledClickDisable") : t("nvrs.disabledClickEnable")}
            title={nvr.enabled ? t("nvrs.enabledClickDisable") : t("nvrs.disabledClickEnable")}
            disabled={update.isPending}
            onClick={() => update.mutate({ id: nvr.id, body: { enabled: !nvr.enabled } })}
            className={[
              "dss-focus relative h-5 w-9 rounded-full transition-colors",
              nvr.enabled ? "bg-accent/90 shadow-glow" : "bg-white/10",
            ].join(" ")}
          >
            <span
              className={[
                "absolute top-0.5 h-4 w-4 rounded-full bg-white transition-all",
                nvr.enabled ? "right-0.5" : "left-0.5",
              ].join(" ")}
            />
          </button>
        )}
        <span className="truncate font-mono text-sm text-ink-soft" title={nvr.id}>
          {nvr.id}
        </span>
        <span className="truncate text-base font-semibold text-ink">{nvr.label}</span>
        <span className="truncate font-mono text-sm text-ink-soft">{nvr.ip}</span>
        <span className="font-mono text-sm text-ink-mute">{nvr.port}</span>
        <span className="truncate text-sm text-ink-mute">{nvr.rtsp_username}</span>
        <select
          aria-label={t("nvrs.vendor")}
          title={t("nvrs.changeVendorTitle")}
          value={pendingVendor ?? nvr.vendor}
          disabled={update.isPending}
          onChange={(e) => changeVendor(e.target.value as Vendor)}
          className="w-full min-w-0 truncate rounded-md border border-white/[.07] bg-panel px-2 py-1.5 text-sm font-semibold text-ink-soft hover:border-white/[.14] disabled:opacity-50"
        >
          {VENDORS.map((v) => (
            <option key={v} value={v}>
              {v}
            </option>
          ))}
        </select>
        <div className="flex items-center gap-1.5">
          <span className="flex h-7 shrink-0 items-center whitespace-nowrap rounded-md border border-accent/20 bg-accent/[.10] px-2 text-xs font-semibold text-accent-light">
            {t("nvrs.channelCount", { count: nvr.camera_count })}
          </span>
          <button
            type="button"
            onClick={runTest}
            disabled={test.isPending}
            className="flex h-7 items-center gap-1 rounded-md border border-white/[.08] bg-panel px-2 text-xs font-semibold text-ink-mute hover:text-ink-soft disabled:opacity-50"
          >
            <PlayIcon size={10} />
            {test.isPending ? "…" : t("nvrs.test")}
          </button>
          <Link
            to={`/nvrs/${nvr.id}/channels`}
            className="flex h-7 items-center gap-1 rounded-md border border-white/[.08] bg-panel px-2 text-xs font-semibold text-ink-mute hover:text-ink-soft"
          >
            <CameraIcon size={11} />
            {t("nvrs.cams")}
          </Link>
          <button
            type="button"
            onClick={() => setEditing((v) => !v)}
            title={t("common.edit")}
            className="flex h-7 w-7 items-center justify-center rounded-md border border-white/[.08] bg-panel text-ink-mute hover:text-ink-soft"
          >
            <PencilIcon size={12} />
          </button>
          <button
            type="button"
            onClick={() => setConfirmDel(true)}
            title={t("common.delete")}
            className="flex h-7 w-7 items-center justify-center rounded-md border border-danger/20 bg-danger/[.10] text-danger hover:bg-danger/20"
          >
            <TrashIcon size={12} />
          </button>
        </div>
      </div>

      {/* vendor-change recovery hint — only while still disabled (the goal is to
          get it enabled; once enabled the effect above clears vendorChanged). */}
      {vendorChanged && !nvr.enabled && (
        <div className="px-3.5 pb-2 text-2xs text-ink-dim">
          {update.isPending
            ? t("nvrs.savingVendor")
            : update.isError
              ? t("nvrs.vendorUpdateFailed", { message: (update.error as Error).message })
              : test.isPending
                ? t("nvrs.vendorChangedRetesting")
                : t("nvrs.vendorChangedHint")}
        </div>
      )}

      {/* inline test result badge */}
      {testResult && (
        <div className="flex items-center gap-2 px-3.5 pb-3">
          <span
            className={[
              "flex items-center gap-1 rounded-md px-2 py-1 text-xs font-semibold",
              testResult.ok
                ? "border border-accent/20 bg-accent/[.10] text-accent-light"
                : "border border-danger/20 bg-danger/[.10] text-danger",
            ].join(" ")}
          >
            {testResult.ok ? <CheckIcon size={11} /> : <XIcon size={11} />}
            {testResult.message}
          </span>
          <button
            type="button"
            onClick={() => setTestResult(null)}
            className="text-ink-faint hover:text-ink-soft"
          >
            <XIcon size={12} />
          </button>
        </div>
      )}

      {/* inline editor */}
      {editing && (
        <EditRow
          nvr={nvr}
          pending={update.isPending}
          error={update.isError ? (update.error as Error).message : null}
          onCancel={() => setEditing(false)}
          onSave={(body) =>
            update.mutate({ id: nvr.id, body }, { onSuccess: () => setEditing(false) })
          }
        />
      )}

      {/* delete confirm */}
      {confirmDel && (
        <div className="flex items-center gap-2 border-t border-white/[.06] px-3.5 py-2.5">
          <span className="text-xs text-ink-soft">{t("nvrs.deleteConfirm", { label: nvr.label })}</span>
          {del.isError && <span className="text-xs text-danger">{(del.error as Error).message}</span>}
          <div className="ml-auto flex gap-2">
            <button
              type="button"
              onClick={() => setConfirmDel(false)}
              className="dss-btn-ghost h-7 px-3 text-xs"
            >
              {t("common.cancel")}
            </button>
            <button
              type="button"
              disabled={del.isPending}
              onClick={() => del.mutate(nvr.id, { onSuccess: () => setConfirmDel(false) })}
              className="dss-btn-danger h-7 px-3 text-xs"
            >
              {del.isPending ? t("nvrs.deleting") : t("common.delete")}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function EditRow({
  nvr,
  pending,
  error,
  onCancel,
  onSave,
}: {
  nvr: Nvr;
  pending: boolean;
  error: string | null;
  onCancel: () => void;
  onSave: (body: { label: string; ip: string; enabled: boolean }) => void;
}) {
  const { t } = useTranslation();
  const [label, setLabel] = useState(nvr.label);
  const [ip, setIp] = useState(nvr.ip);
  const [enabled, setEnabled] = useState(nvr.enabled);
  return (
    <div className="flex flex-wrap items-end gap-3 border-t border-white/[.06] px-3.5 py-3">
      <label className="min-w-[160px] flex-1">
        <span className="mb-1 block text-2xs font-semibold text-ink-dim">{t("nvrs.label")}</span>
        <input className="dss-input h-9" value={label} onChange={(e) => setLabel(e.target.value)} />
      </label>
      <label className="min-w-[140px] flex-1">
        <span className="mb-1 block text-2xs font-semibold text-ink-dim">{t("nvrs.ip")}</span>
        <input
          className="dss-input h-9 font-mono"
          value={ip}
          onChange={(e) => setIp(e.target.value)}
        />
      </label>
      <label className="flex items-center gap-2 pb-2 text-xs text-ink-soft">
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => setEnabled(e.target.checked)}
          className="accent-accent"
        />
        {t("common.enabled")}
      </label>
      {error && <span className="pb-2 text-xs text-danger">{error}</span>}
      <div className="ml-auto flex gap-2 pb-0.5">
        <button type="button" onClick={onCancel} className="dss-btn-ghost h-9 px-3 text-xs">
          {t("common.cancel")}
        </button>
        <button
          type="button"
          disabled={pending}
          onClick={() => onSave({ label: label.trim(), ip: ip.trim(), enabled })}
          className="dss-btn-primary h-9 px-4 text-xs"
        >
          {pending ? t("nvrs.saving") : t("common.save")}
        </button>
      </div>
    </div>
  );
}

function Dot({ ok, title }: { ok?: boolean; title?: string }) {
  const cls =
    ok === undefined
      ? "bg-ink-faint"
      : ok
        ? "bg-accent shadow-glow"
        : "bg-danger shadow-[0_0_7px_#e76b5e]";
  return <span title={title} className={`h-2.5 w-2.5 rounded-full ${cls}`} />;
}
