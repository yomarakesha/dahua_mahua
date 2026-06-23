import { useEffect } from "react";
import { MsePlayer } from "@/components/video/MsePlayer";
import { streamName } from "@/api/types";
import { XIcon } from "@/components/icons";
import type { Camera } from "@/api/types";

interface Props {
  cam: Camera;
  onClose: () => void;
}

/** Single-camera fullscreen overlay. Uses MAIN stream when available. */
export function FullscreenView({ cam, onClose }: Props) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const quality = cam.has_main ? "main" : cam.has_sub ? "sub" : null;

  return (
    <div
      className="fixed inset-0 z-50 flex flex-col bg-black/95 backdrop-blur-sm"
      onClick={onClose}
    >
      <div className="flex flex-none items-center gap-3 px-6 py-4" onClick={(e) => e.stopPropagation()}>
        <span className="h-2 w-2 animate-pulse rounded-full bg-accent shadow-[0_0_8px_#2ecc71]" />
        <span className="text-base font-bold text-ink-bright">{cam.display_name}</span>
        <span className="font-mono text-2xs text-ink-faint">ch{cam.channel}</span>
        <button
          type="button"
          onClick={onClose}
          title="Close (Esc)"
          className="ml-auto flex h-9 w-9 items-center justify-center rounded-lg border border-white/[.08] bg-white/[.04] text-ink-mute transition hover:bg-white/[.08] hover:text-ink"
        >
          <XIcon size={18} />
        </button>
      </div>

      <div
        className="relative min-h-0 flex-1 px-6 pb-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="relative h-full w-full overflow-hidden rounded-xl border border-white/[.08] bg-black">
          {quality ? (
            <MsePlayer src={streamName(cam, quality)} className="absolute inset-0 h-full w-full" />
          ) : (
            <div className="absolute inset-0 flex items-center justify-center">
              <span className="font-mono text-xs uppercase tracking-wider text-ink-faint">
                no stream available
              </span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
