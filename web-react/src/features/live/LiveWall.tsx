import { useEffect, useMemo, useState } from "react";
import { useCameras, useNvrs } from "@/api/hooks";
import { CONFIG } from "@/lib/config";
import { ChevronRight } from "@/components/icons";
import type { Camera } from "@/api/types";
import { LiveTopbar } from "./LiveTopbar";
import { LiveSidebar } from "./LiveSidebar";
import { CameraTile } from "./CameraTile";
import { FullscreenView } from "./FullscreenView";
import { useClock } from "./useClock";

const PRESETS = CONFIG.gridPresets;
const PATROL = CONFIG.patrolIntervals;

export default function LiveWall() {
  const { data: cameras, isLoading: camsLoading } = useCameras();
  const { data: nvrs } = useNvrs();

  const [presetIdx, setPresetIdx] = useState(3); // default 16 (4×4)
  const [selectedNvrId, setSelectedNvrId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [patrol, setPatrol] = useState(false);
  const [patrolIdx, setPatrolIdx] = useState(1); // 10s
  const [page, setPage] = useState(0);
  const [fullscreen, setFullscreen] = useState<Camera | null>(null);

  const time = useClock();

  const cellCount = PRESETS[presetIdx];
  const gridN = Math.round(Math.sqrt(cellCount));
  const patrolInterval = PATROL[patrolIdx];

  // Enabled cameras only, optionally filtered by NVR + search text.
  const enabled = useMemo(
    () => (cameras ?? []).filter((c) => c.enabled),
    [cameras],
  );

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return enabled.filter((c) => {
      if (selectedNvrId && c.nvr_id !== selectedNvrId) return false;
      if (q && !c.display_name.toLowerCase().includes(q) && !(c.name ?? "").toLowerCase().includes(q))
        return false;
      return true;
    });
  }, [enabled, selectedNvrId, search]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / cellCount));

  // Keep page in range whenever filters/layout change.
  useEffect(() => {
    setPage((p) => Math.min(p, totalPages - 1));
  }, [totalPages]);

  // Reset to first page on any filter/layout change.
  useEffect(() => {
    setPage(0);
  }, [selectedNvrId, search, presetIdx]);

  const pageCams = useMemo(
    () => filtered.slice(page * cellCount, page * cellCount + cellCount),
    [filtered, page, cellCount],
  );

  // Patrol: auto-advance pages when more than one exists.
  useEffect(() => {
    if (!patrol || totalPages <= 1) return;
    const id = window.setInterval(
      () => setPage((p) => (p + 1) % totalPages),
      patrolInterval * 1000,
    );
    return () => window.clearInterval(id);
  }, [patrol, totalPages, patrolInterval]);

  // Counts for sidebar (enabled cameras per nvr) + a load proxy.
  const countByNvr = useMemo(() => {
    const m: Record<string, number> = {};
    for (const c of enabled) m[c.nvr_id] = (m[c.nvr_id] ?? 0) + 1;
    return m;
  }, [enabled]);

  const healthyById = useMemo(() => {
    const m: Record<string, boolean> = {};
    for (const n of nvrs ?? []) m[n.id] = n.enabled;
    return m;
  }, [nvrs]);

  const visibleStreams = pageCams.length;
  const total = enabled.length;
  const online = filtered.length; // streams we are attempting/showing
  // Load proxy: how full the current page is vs. the grid capacity.
  const load = Math.min(1, visibleStreams / cellCount);

  return (
    <div className="flex h-full w-full flex-col bg-deep">
      <LiveTopbar
        gridN={gridN}
        onCycleGrid={() => setPresetIdx((i) => (i + 1) % PRESETS.length)}
        patrol={patrol}
        onTogglePatrol={() => setPatrol((p) => !p)}
        patrolInterval={patrolInterval}
        onCyclePatrolInterval={() => setPatrolIdx((i) => (i + 1) % PATROL.length)}
        search={search}
        onSearch={setSearch}
        online={online}
        total={total}
      />

      <div className="flex min-h-0 flex-1">
        <LiveSidebar
          nvrs={nvrs ?? []}
          countByNvr={countByNvr}
          healthyById={healthyById}
          selectedNvrId={selectedNvrId}
          onSelectNvr={setSelectedNvrId}
          visibleStreams={visibleStreams}
          load={load}
        />

        <div className="relative flex min-h-0 min-w-0 flex-1 flex-col bg-deep">
          {camsLoading ? (
            <SkeletonGrid n={gridN} />
          ) : filtered.length === 0 ? (
            <EmptyState filtered={enabled.length > 0} />
          ) : (
            <div
              className="grid min-h-0 flex-1 gap-1.5 p-2"
              style={{
                gridTemplateColumns: `repeat(${gridN}, minmax(0, 1fr))`,
                gridTemplateRows: `repeat(${gridN}, minmax(0, 1fr))`,
              }}
            >
              {pageCams.map((cam) => (
                <CameraTile key={cam.id} cam={cam} time={time} onOpen={setFullscreen} />
              ))}
            </div>
          )}

          {/* page indicator / pager */}
          {totalPages > 1 && (
            <div className="pointer-events-none absolute bottom-3 left-1/2 flex -translate-x-1/2 items-center gap-2">
              <button
                type="button"
                onClick={() => setPage((p) => (p - 1 + totalPages) % totalPages)}
                className="pointer-events-auto flex h-7 w-7 rotate-180 items-center justify-center rounded-lg border border-white/[.08] bg-panel/90 text-ink-mute transition hover:text-ink-soft"
                title="Previous page"
              >
                <ChevronRight size={14} />
              </button>
              <span className="pointer-events-auto rounded-lg border border-white/[.08] bg-panel/90 px-3 py-1 font-mono text-2xs text-ink-mute">
                {page + 1}/{totalPages}
                {filtered.length > cellCount && (
                  <span className="text-ink-faint">
                    {" "}
                    · +{filtered.length - cellCount} more
                  </span>
                )}
              </span>
              <button
                type="button"
                onClick={() => setPage((p) => (p + 1) % totalPages)}
                className="pointer-events-auto flex h-7 w-7 items-center justify-center rounded-lg border border-white/[.08] bg-panel/90 text-ink-mute transition hover:text-ink-soft"
                title="Next page"
              >
                <ChevronRight size={14} />
              </button>
            </div>
          )}
        </div>
      </div>

      <StatusBar
        online={online}
        connecting={Math.max(0, total - online)}
        total={total}
        nvrLabel={selectedNvrName(nvrs ?? [], selectedNvrId)}
        time={time}
      />

      {fullscreen && (
        <FullscreenView cam={fullscreen} onClose={() => setFullscreen(null)} />
      )}
    </div>
  );
}

function selectedNvrName(
  nvrs: { id: string; label: string }[],
  id: string | null,
): string {
  if (!id) return "ALL NVRS";
  return nvrs.find((n) => n.id === id)?.label ?? id;
}

function StatusBar({
  online,
  connecting,
  total,
  nvrLabel,
  time,
}: {
  online: number;
  connecting: number;
  total: number;
  nvrLabel: string;
  time: string;
}) {
  return (
    <div className="flex h-8 flex-none items-center gap-5 border-t border-white/[.06] bg-gradient-to-b from-[#0c1014] to-[#090c0f] px-4 font-mono text-xs">
      <span className="flex items-center gap-1.5 text-accent-light">
        <span className="h-1.5 w-1.5 rounded-full bg-accent shadow-[0_0_7px_#2ecc71]" />
        Online: {online}
      </span>
      <span className="flex items-center gap-1.5 text-warn">
        <span className="h-1.5 w-1.5 rounded-full bg-warn" />
        Connecting: {connecting}
      </span>
      <span className="flex items-center gap-1.5 text-ink-dim">
        <span className="h-1.5 w-1.5 rounded-full bg-ink-faint/60" />
        Error: 0
      </span>
      <span className="text-ink-faint">Total: {total}</span>
      <span className="ml-auto truncate text-[#3f4951]">
        {nvrLabel} · {time}
      </span>
    </div>
  );
}

function SkeletonGrid({ n }: { n: number }) {
  return (
    <div
      className="grid min-h-0 flex-1 gap-1.5 p-2"
      style={{
        gridTemplateColumns: `repeat(${n}, minmax(0, 1fr))`,
        gridTemplateRows: `repeat(${n}, minmax(0, 1fr))`,
      }}
    >
      {Array.from({ length: n * n }).map((_, i) => (
        <div
          key={i}
          className="animate-pulse rounded border border-white/[.04] bg-white/[.02]"
        />
      ))}
    </div>
  );
}

function EmptyState({ filtered }: { filtered: boolean }) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-2 text-center">
      <div className="text-base font-semibold text-ink-mute">
        {filtered ? "No cameras match your filters" : "No cameras available"}
      </div>
      <div className="text-2xs text-ink-faint">
        {filtered
          ? "Try clearing the search or NVR filter."
          : "Add an NVR and enable channels to populate the wall."}
      </div>
    </div>
  );
}
