import { useEffect, useRef, useState } from "react";
import { CONFIG } from "@/lib/config";
import { WebCodecsEngine, type EngineStatus } from "@/lib/video/webcodecs-engine";

export type { EngineStatus };

interface Props {
  /** go2rtc stream name, e.g. `nvr-…_ch3_main`. */
  src: string;
  className?: string;
  /** Mirrors MsePlayer.onStatus so FullscreenView can drive one status badge and
   *  fall back to MSE when WebCodecs can't go live. */
  onStatus?: (status: EngineStatus) => void;
}

/**
 * Renders the 4MP main via WebCodecs (hardware decode + drop-late frame policy)
 * onto a <canvas>. Video-only — no audio path (turning sound on falls back to MSE
 * in FullscreenView). See WebCodecsEngine for the why.
 */
export function WebCodecsPlayer({ src, className, onStatus }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [status, setStatus] = useState<EngineStatus>("connecting");

  useEffect(() => {
    onStatus?.(status);
  }, [status, onStatus]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !src) return;
    setStatus("connecting");
    const engine = new WebCodecsEngine(canvas, { onStatus: setStatus });
    engine.start(`${CONFIG.go2rtcWsBase}/api/ws?src=${encodeURIComponent(src)}`);
    return () => engine.destroy();
  }, [src]);

  return (
    <div className={`${className ?? ""} overflow-hidden`}>
      <canvas
        ref={canvasRef}
        className="absolute inset-0 h-full w-full"
        style={{ objectFit: "contain" }}
      />
      {status !== "live" && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center bg-black/30">
          {status === "connecting" ? (
            <span className="flex items-center gap-2 font-mono text-3xs uppercase tracking-wider text-ink-faint">
              <span className="h-3 w-3 animate-spin rounded-full border border-ink-faint/50 border-t-transparent" />
              connecting
            </span>
          ) : (
            <span className="flex items-center gap-1.5 rounded border border-danger/40 bg-danger/[.14] px-2 py-1 font-mono text-3xs font-bold uppercase tracking-wider text-danger">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-danger" />
              signal lost
            </span>
          )}
        </div>
      )}
    </div>
  );
}
