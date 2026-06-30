import { useState } from "react";
import { Link } from "react-router-dom";
import { useDeleteNvr, useTestNvr, useUpdateNvr } from "@/api/hooks";
import type { Nvr, NvrHealthResult, NvrTestResult } from "@/api/types";
import { CameraIcon, CheckIcon, PencilIcon, PlayIcon, TrashIcon, XIcon } from "@/components/icons";

const GRID = "grid-cols-[40px_1.3fr_1.2fr_1.3fr_.6fr_1fr_1fr_1.6fr]";

interface Props {
  nvrs: Nvr[];
  showHealth: boolean;
  health: Record<string, NvrHealthResult>;
}

/** NVR list table. Per-row: health dot, channel count, Test, Cams, edit, delete. */
export function NvrTable({ nvrs, showHealth, health }: Props) {
  if (nvrs.length === 0) {
    return (
      <div className="rounded-xl border border-white/[.06] bg-deep/60 px-4 py-10 text-center text-sm text-ink-dim">
        No recorders yet — add one above.
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
          <span>{showHealth ? "OK" : "ON"}</span>
          <span>ID</span>
          <span>Label</span>
          <span>IP</span>
          <span>Port</span>
          <span>User</span>
          <span>Vendor</span>
          <span>Actions</span>
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
  const test = useTestNvr();
  const update = useUpdateNvr();
  const del = useDeleteNvr();
  const [editing, setEditing] = useState(false);
  const [confirmDel, setConfirmDel] = useState(false);
  const [testResult, setTestResult] = useState<NvrTestResult | null>(null);

  function runTest() {
    test.mutate(nvr.id, {
      onSuccess: (r) => setTestResult(r),
      onError: (e) => setTestResult({ ok: false, message: (e as Error).message, banned_until: null, remaining: null }),
    });
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
            aria-label={nvr.enabled ? "Enabled — click to disable" : "Disabled — click to enable"}
            title={nvr.enabled ? "Enabled — click to disable" : "Disabled — click to enable"}
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
        <span className="truncate rounded-md border border-white/[.07] bg-panel px-2.5 py-1.5 text-sm font-semibold text-ink-soft">
          {nvr.vendor}
        </span>
        <div className="flex items-center gap-1.5">
          <span className="flex h-7 items-center rounded-md border border-accent/20 bg-accent/[.10] px-2 text-xs font-semibold text-accent-light">
            {nvr.camera_count} ch
          </span>
          <button
            type="button"
            onClick={runTest}
            disabled={test.isPending}
            className="flex h-7 items-center gap-1 rounded-md border border-white/[.08] bg-panel px-2 text-xs font-semibold text-ink-mute hover:text-ink-soft disabled:opacity-50"
          >
            <PlayIcon size={10} />
            {test.isPending ? "…" : "Test"}
          </button>
          <Link
            to={`/nvrs/${nvr.id}/channels`}
            className="flex h-7 items-center gap-1 rounded-md border border-white/[.08] bg-panel px-2 text-xs font-semibold text-ink-mute hover:text-ink-soft"
          >
            <CameraIcon size={11} />
            Cams
          </Link>
          <button
            type="button"
            onClick={() => setEditing((v) => !v)}
            title="Edit"
            className="flex h-7 w-7 items-center justify-center rounded-md border border-white/[.08] bg-panel text-ink-mute hover:text-ink-soft"
          >
            <PencilIcon size={12} />
          </button>
          <button
            type="button"
            onClick={() => setConfirmDel(true)}
            title="Delete"
            className="flex h-7 w-7 items-center justify-center rounded-md border border-danger/20 bg-danger/[.10] text-danger hover:bg-danger/20"
          >
            <TrashIcon size={12} />
          </button>
        </div>
      </div>

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
          <span className="text-xs text-ink-soft">Delete {nvr.label}? This removes its cameras.</span>
          {del.isError && <span className="text-xs text-danger">{(del.error as Error).message}</span>}
          <div className="ml-auto flex gap-2">
            <button
              type="button"
              onClick={() => setConfirmDel(false)}
              className="dss-btn-ghost h-7 px-3 text-xs"
            >
              Cancel
            </button>
            <button
              type="button"
              disabled={del.isPending}
              onClick={() => del.mutate(nvr.id, { onSuccess: () => setConfirmDel(false) })}
              className="dss-btn-danger h-7 px-3 text-xs"
            >
              {del.isPending ? "Deleting…" : "Delete"}
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
  const [label, setLabel] = useState(nvr.label);
  const [ip, setIp] = useState(nvr.ip);
  const [enabled, setEnabled] = useState(nvr.enabled);
  return (
    <div className="flex flex-wrap items-end gap-3 border-t border-white/[.06] px-3.5 py-3">
      <label className="min-w-[160px] flex-1">
        <span className="mb-1 block text-2xs font-semibold text-ink-dim">Label</span>
        <input className="dss-input h-9" value={label} onChange={(e) => setLabel(e.target.value)} />
      </label>
      <label className="min-w-[140px] flex-1">
        <span className="mb-1 block text-2xs font-semibold text-ink-dim">IP</span>
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
        Enabled
      </label>
      {error && <span className="pb-2 text-xs text-danger">{error}</span>}
      <div className="ml-auto flex gap-2 pb-0.5">
        <button type="button" onClick={onCancel} className="dss-btn-ghost h-9 px-3 text-xs">
          Cancel
        </button>
        <button
          type="button"
          disabled={pending}
          onClick={() => onSave({ label: label.trim(), ip: ip.trim(), enabled })}
          className="dss-btn-primary h-9 px-4 text-xs"
        >
          {pending ? "Saving…" : "Save"}
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
