import { useMemo, useState } from "react";
import { useEvents, useNvrHealth, useNvrs, useReconcile } from "@/api/hooks";
import type { NvrHealthResult } from "@/api/types";
import { ActivityIcon, CheckIcon, RefreshIcon, ServerIcon } from "@/components/icons";
import { AddNvrForm } from "./AddNvrForm";
import { NvrTable } from "./NvrTable";
import { PerformancePanel } from "./PerformancePanel";

/** Admin screen: add / configure / monitor NVRs (recorders). */
export default function NvrManagement() {
  const nvrs = useNvrs();
  const [showHealth, setShowHealth] = useState(false);
  const [showEvents, setShowEvents] = useState(false);
  const reconcile = useReconcile();
  const health = useNvrHealth(showHealth);

  const healthMap = useMemo<Record<string, NvrHealthResult>>(() => {
    const m: Record<string, NvrHealthResult> = {};
    for (const h of health.data ?? []) m[h.nvr_id] = h;
    return m;
  }, [health.data]);

  const list = nvrs.data ?? [];

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex max-w-[1100px] flex-col gap-4 p-5 lg:p-7">
        {/* header */}
        <header className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg border border-accent/25 bg-accent/[.12] text-accent-light">
            <ServerIcon size={18} />
          </div>
          <div>
            <h1 className="text-[17px] font-extrabold text-ink-bright">NVR Management</h1>
            <p className="text-sm text-ink-dim">Add, configure and monitor recorders</p>
          </div>
        </header>

        <AddNvrForm />

        {/* NVRS table panel */}
        <section className="dss-panel p-4">
          <div className="mb-3 flex items-center justify-between">
            <span className="dss-label tracking-[1.4px]">
              NVRS ({nvrs.isLoading ? "…" : list.length})
            </span>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setShowHealth((v) => !v)}
                className={[
                  "flex h-[30px] items-center gap-1.5 rounded-md px-3 text-sm font-semibold transition",
                  showHealth
                    ? "border border-accent/25 bg-accent/[.12] text-accent-light"
                    : "border border-white/[.07] bg-panel text-ink-mute hover:text-ink-soft",
                ].join(" ")}
              >
                <ActivityIcon size={12} />
                Health
              </button>
              <button
                type="button"
                disabled={reconcile.isPending}
                onClick={() => reconcile.mutate(false)}
                className="flex h-[30px] items-center gap-1.5 rounded-md border border-white/[.07] bg-panel px-3 text-sm font-semibold text-ink-mute hover:text-ink-soft disabled:opacity-50"
              >
                {reconcile.isPending ? (
                  "Reconciling…"
                ) : reconcile.isSuccess ? (
                  <>
                    <CheckIcon size={12} /> Reconciled
                  </>
                ) : (
                  "Reconcile"
                )}
              </button>
              <button
                type="button"
                onClick={() => setShowEvents((v) => !v)}
                className={[
                  "flex h-[30px] items-center rounded-md px-3 text-sm font-semibold transition",
                  showEvents
                    ? "border border-accent/25 bg-accent/[.12] text-accent-light"
                    : "border border-white/[.07] bg-panel text-ink-mute hover:text-ink-soft",
                ].join(" ")}
              >
                Events
              </button>
              <button
                type="button"
                title="Refresh"
                disabled={nvrs.isFetching}
                onClick={() => void nvrs.refetch()}
                className="flex h-[30px] w-[30px] items-center justify-center rounded-md border border-white/[.07] bg-panel text-ink-mute hover:text-ink-soft disabled:opacity-50"
              >
                <RefreshIcon size={13} className={nvrs.isFetching ? "animate-spin" : ""} />
              </button>
            </div>
          </div>

          {reconcile.isError && (
            <p className="mb-2 text-xs text-danger">{(reconcile.error as Error).message}</p>
          )}

          {nvrs.isLoading ? (
            <TableSkeleton />
          ) : nvrs.isError ? (
            <p className="px-2 py-6 text-sm text-danger">
              Failed to load NVRs: {(nvrs.error as Error).message}
            </p>
          ) : (
            <NvrTable nvrs={list} showHealth={showHealth} health={healthMap} />
          )}

          {showEvents && <EventsPanel />}
        </section>

        <PerformancePanel />
      </div>
    </div>
  );
}

function EventsPanel() {
  const events = useEvents(50);
  return (
    <div className="mt-4 rounded-xl border border-white/[.06] bg-deep/60 p-3">
      <div className="mb-2 text-2xs font-extrabold uppercase tracking-[1.2px] text-ink-faint">
        Recent events
      </div>
      {events.isLoading ? (
        <p className="text-sm text-ink-dim">Loading…</p>
      ) : events.isError ? (
        <p className="text-sm text-danger">{(events.error as Error).message}</p>
      ) : (events.data ?? []).length === 0 ? (
        <p className="text-sm text-ink-dim">No events recorded.</p>
      ) : (
        <ul className="max-h-64 space-y-1 overflow-y-auto">
          {(events.data ?? []).map((e) => (
            <li key={e.id} className="flex items-start gap-3 text-xs">
              <span className="shrink-0 font-mono text-ink-faint">
                {new Date(e.created_at).toLocaleTimeString()}
              </span>
              <span className="shrink-0 font-semibold text-accent-light">{e.event_type}</span>
              <span className="truncate text-ink-mute" title={e.message ?? ""}>
                {e.message ?? e.ip}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function TableSkeleton() {
  return (
    <div className="space-y-2">
      {[0, 1, 2].map((i) => (
        <div
          key={i}
          className="h-12 animate-pulse rounded-xl border border-white/[.06] bg-deep/60"
        />
      ))}
    </div>
  );
}
