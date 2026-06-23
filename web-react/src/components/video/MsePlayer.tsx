import { useEffect, useRef } from "react";
import { CONFIG } from "@/lib/config";
import { registerDssMse } from "./dss-mse";
import type { VideoRTC } from "@/lib/vendor/video-rtc.js";

registerDssMse();

interface Props {
  /** go2rtc stream name, e.g. `nvr-…_ch3` (sub) or `nvr-…_ch3_main`. */
  src: string;
  className?: string;
}

/**
 * Buffered-MSE video tile. Wraps the <dss-mse> web component: mounts it once,
 * re-points `.src` when the stream changes, and tears it down on unmount (the
 * element owns its WebSocket + MediaSource + reconnect lifecycle).
 */
export function MsePlayer({ src, className }: Props) {
  const hostRef = useRef<HTMLDivElement>(null);
  const elRef = useRef<VideoRTC | null>(null);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const el = document.createElement("dss-mse") as VideoRTC;
    el.mode = "mse";
    el.background = true;
    host.appendChild(el);
    elRef.current = el;
    return () => {
      try {
        el.src = "";
      } catch {
        /* ignore */
      }
      el.remove();
      elRef.current = null;
    };
  }, []);

  useEffect(() => {
    const el = elRef.current;
    if (!el || !src) return;
    el.src = new URL(`${CONFIG.go2rtcWsBase}/api/ws?src=${encodeURIComponent(src)}`);
  }, [src]);

  return <div ref={hostRef} className={className} />;
}
