import { useCameras, useNvrs } from "@/api/hooks";
import type { Camera } from "@/api/types";

/** Honest "concurrent streams" estimate from enabled cameras + their stream variants. */
export function PerformancePanel() {
  const cameras = useCameras();
  const nvrs = useNvrs();

  const cams: Camera[] = cameras.data ?? [];
  const enabledCams = cams.filter((c) => c.enabled);
  // each enabled camera streams the variants it exposes (sub and/or main)
  const concurrent = enabledCams.reduce(
    (sum, c) => sum + (c.has_sub ? 1 : 0) + (c.has_main ? 1 : 0),
    0,
  );
  const nvrCount = nvrs.data?.length ?? 0;

  return (
    <div className="rounded-2xl border border-white/[.06] bg-white/[.02] px-4 py-4">
      <div className="mb-3 text-2xs font-extrabold uppercase tracking-[1.2px] text-ink-faint">
        Performance
      </div>
      <div className="flex items-center gap-3">
        <span className="text-base text-ink-mute">Concurrent streams</span>
        <div className="flex h-9 min-w-[64px] items-center justify-center rounded-lg border border-white/[.07] bg-deep px-3 font-mono text-base font-bold text-ink">
          {cameras.isLoading ? "…" : concurrent}
        </div>
      </div>
      <div className="mt-2 text-xs text-ink-dim">
        {nvrCount} NVR{nvrCount === 1 ? "" : "s"} · {enabledCams.length} of {cams.length} cameras
        enabled
      </div>
    </div>
  );
}
