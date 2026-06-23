import { useState } from "react";
import { ChevronRight, PlusIcon } from "@/components/icons";
import type { Camera, Nvr } from "@/api/types";

interface Props {
  nvrs: Nvr[];
  /** Enabled cameras (used for the expandable per-NVR camera list). */
  cameras: Camera[];
  /** Camera count per nvr id (enabled cameras visible in the wall). */
  countByNvr: Record<string, number>;
  /** nvr ids considered healthy/up (defaults to enabled). */
  healthyById: Record<string, boolean>;
  selectedNvrId: string | null;
  onSelectNvr: (id: string | null) => void;
  /** Open a camera fullscreen (from the expanded tree). */
  onPickCamera: (cam: Camera) => void;
  /** Number of players currently mounted (for the load card). */
  visibleStreams: number;
  /** 0..1 system-load proxy. */
  load: number;
}

const VENDOR_TAG: Record<string, string> = { dahua: "DAH", hikvision: "HIK" };

export function LiveSidebar({
  nvrs,
  cameras,
  countByNvr,
  healthyById,
  selectedNvrId,
  onSelectNvr,
  onPickCamera,
  visibleStreams,
  load,
}: Props) {
  const pct = Math.round(load * 100);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const toggleExpand = (id: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  return (
    <aside className="flex w-[210px] flex-none flex-col gap-1.5 overflow-hidden border-r border-white/[.06] bg-gradient-to-b from-[#0c1014] to-[#090c0f] px-3 py-3.5">
      <div className="flex items-center justify-between px-1 pb-1">
        <span className="text-xs font-extrabold tracking-[1.4px] text-ink-faint">NVRS</span>
        <button
          type="button"
          onClick={() => onSelectNvr(null)}
          className={[
            "rounded-md border px-2.5 py-0.5 text-2xs font-bold transition",
            selectedNvrId === null
              ? "border-accent/25 bg-accent/[.12] text-accent-light"
              : "border-white/[.06] bg-white/[.04] text-ink-dim hover:text-ink-soft",
          ].join(" ")}
        >
          All
        </button>
      </div>

      <div className="flex min-h-0 flex-1 flex-col gap-1.5 overflow-y-auto">
        {nvrs.length === 0 ? (
          <div className="px-1 py-4 text-2xs text-ink-faint">No recorders.</div>
        ) : (
          nvrs.map((n) => {
            const active = selectedNvrId === n.id;
            const healthy = healthyById[n.id] ?? n.enabled;
            const isOpen = expanded.has(n.id);
            const cams = cameras
              .filter((c) => c.nvr_id === n.id)
              .sort((a, b) => a.channel - b.channel);
            return (
              <div key={n.id} className="flex flex-col">
                <div
                  className={[
                    "flex h-10 items-center gap-1 rounded-lg border pr-2.5 transition",
                    active
                      ? "border-accent/30 bg-accent/[.10]"
                      : "border-white/[.06] bg-white/[.02] hover:bg-white/[.04]",
                  ].join(" ")}
                >
                  <button
                    type="button"
                    aria-label={isOpen ? "Collapse" : "Expand"}
                    onClick={() => toggleExpand(n.id)}
                    className="flex h-full w-7 flex-none items-center justify-center text-ink-dim hover:text-ink-soft"
                  >
                    <ChevronRight
                      size={13}
                      className={["transition-transform", isOpen ? "rotate-90" : ""].join(" ")}
                    />
                  </button>
                  <button
                    type="button"
                    onClick={() => onSelectNvr(active ? null : n.id)}
                    className="flex h-full flex-1 items-center gap-2.5 overflow-hidden text-left"
                  >
                    <span
                      className={[
                        "h-1.5 w-1.5 flex-none rounded-full",
                        healthy ? "bg-accent shadow-[0_0_7px_#2ecc71]" : "bg-ink-faint/60",
                      ].join(" ")}
                    />
                    <span className="flex-1 truncate text-base font-semibold text-ink-soft">
                      {n.label}
                    </span>
                    <span className="font-mono text-xs text-ink-faint">{countByNvr[n.id] ?? 0}</span>
                    <span className="rounded-sm bg-white/[.05] px-1.5 py-0.5 text-3xs font-bold tracking-wide text-ink-dim">
                      {VENDOR_TAG[n.vendor] ?? n.vendor.slice(0, 3).toUpperCase()}
                    </span>
                  </button>
                </div>

                {isOpen && (
                  <div className="mt-1 flex flex-col gap-0.5 pl-7">
                    {cams.length === 0 ? (
                      <div className="px-2 py-1.5 text-3xs text-ink-faint">No cameras.</div>
                    ) : (
                      cams.map((c) => (
                        <button
                          key={c.id}
                          type="button"
                          onClick={() => onPickCamera(c)}
                          title={`Open ${c.display_name} fullscreen`}
                          className="flex items-center gap-2 rounded-md px-2 py-1 text-left hover:bg-white/[.05]"
                        >
                          <span className="w-7 flex-none font-mono text-3xs text-ink-faint">
                            ch{c.channel}
                          </span>
                          <span className="flex-1 truncate text-2xs text-ink-mute">
                            {c.display_name}
                          </span>
                        </button>
                      ))
                    )}
                  </div>
                )}
              </div>
            );
          })
        )}

        <div className="mt-1.5 flex items-center justify-between px-1 pb-1 pt-3.5">
          <span className="text-xs font-extrabold tracking-[1.4px] text-ink-faint">GROUPS</span>
          <span className="flex h-5 w-5 items-center justify-center rounded-md bg-white/[.05] text-ink-dim">
            <PlusIcon size={12} />
          </span>
        </div>
      </div>

      <div className="rounded-xl border border-accent/[.16] bg-accent/[.06] p-3">
        <div className="mb-2 text-2xs font-bold tracking-[1px] text-ink-dim">SYSTEM LOAD</div>
        <div className="mb-1.5 flex items-center gap-2">
          <div className="h-[5px] flex-1 overflow-hidden rounded-[3px] bg-white/[.07]">
            <div
              className="h-full rounded-[3px] bg-gradient-to-r from-accent-dark to-accent-light transition-[width] duration-500"
              style={{ width: `${pct}%` }}
            />
          </div>
          <span className="font-mono text-2xs text-ink-mute">{pct}%</span>
        </div>
        <div className="font-mono text-2xs text-ink-faint">
          {visibleStreams} {visibleStreams === 1 ? "stream" : "streams"}
        </div>
      </div>
    </aside>
  );
}
