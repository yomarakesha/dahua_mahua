import { useEffect, useState } from "react";
import { MsePlayer } from "@/components/video/MsePlayer";
import { streamName } from "@/api/types";
import { XIcon, VolumeOn, VolumeOff, ServerIcon, CameraIcon } from "@/components/icons";
import type { Camera } from "@/api/types";

interface Props {
  cam: Camera;
  onClose: () => void;
}

/** Single-camera fullscreen overlay. Uses MAIN stream when available. */
export function FullscreenView({ cam, onClose }: Props) {
  // Audio is OFF by default; the user enables it with the speaker button (a
  // user gesture, which browsers require to start audio). Only here in the
  // main/fullscreen view — grid tiles stay muted.
  const [audioOn, setAudioOn] = useState(false);
  // Source for the MAIN stream: DIRECT from the camera IP by default. The NVR's
  // RTSP relay drops packets / times out on concurrent 4MP mains (measured 7815
  // lost vs 0 direct — exactly why the June-23 build was stable and why routing
  // mains via the NVR froze them). The toggle still offers Via-NVR (`_main_nvr`)
  // as a per-camera fallback when a camera isn't directly reachable.
  const [viaNvr, setViaNvr] = useState(false);

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

        <div className="ml-auto flex items-center gap-2">
          {quality === "main" && (
            <button
              type="button"
              onClick={() => setViaNvr((v) => !v)}
              title={viaNvr ? "Source: via NVR — switch to direct camera" : "Source: direct camera — switch to via NVR"}
              className="flex h-9 items-center gap-2 rounded-lg border border-white/[.08] bg-white/[.04] px-3 text-sm font-semibold text-ink-mute transition hover:bg-white/[.08] hover:text-ink"
            >
              {viaNvr ? <ServerIcon size={15} /> : <CameraIcon size={15} />}
              {viaNvr ? "Via NVR" : "Direct"}
            </button>
          )}
          <button
            type="button"
            onClick={() => setAudioOn((v) => !v)}
            title={audioOn ? "Mute" : "Enable sound"}
            className={[
              "flex h-9 items-center gap-2 rounded-lg border px-3 text-sm font-semibold transition",
              audioOn
                ? "border-accent/30 bg-accent/[.12] text-accent-light"
                : "border-white/[.08] bg-white/[.04] text-ink-mute hover:bg-white/[.08] hover:text-ink",
            ].join(" ")}
          >
            {audioOn ? <VolumeOn size={16} /> : <VolumeOff size={16} />}
            {audioOn ? "Sound on" : "Sound off"}
          </button>
          <button
            type="button"
            onClick={onClose}
            title="Close (Esc)"
            className="flex h-9 w-9 items-center justify-center rounded-lg border border-white/[.08] bg-white/[.04] text-ink-mute transition hover:bg-white/[.08] hover:text-ink"
          >
            <XIcon size={18} />
          </button>
        </div>
      </div>

      <div
        className="relative min-h-0 flex-1 px-6 pb-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="relative h-full w-full overflow-hidden rounded-xl border border-white/[.08] bg-black">
          {quality ? (
            <MsePlayer
              src={streamName(cam, quality, viaNvr)}
              muted={!audioOn}
              className="absolute inset-0 h-full w-full"
            />
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
