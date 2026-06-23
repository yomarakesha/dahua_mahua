import { useEffect, useRef } from "react";
import { CONFIG } from "@/lib/config";
import { registerDssMse } from "./dss-mse";
import type { VideoRTC } from "@/lib/vendor/video-rtc.js";

registerDssMse();

interface Props {
  /** go2rtc stream name, e.g. `nvr-…_ch3` (sub) or `nvr-…_ch3_main`. */
  src: string;
  className?: string;
  /** Audio mute. Defaults true (grid tiles are always muted; fullscreen can
   *  unmute on a user gesture). */
  muted?: boolean;
}

/**
 * Buffered-MSE video tile. Wraps the <dss-mse> web component: mounts it once,
 * re-points `.src` when the stream changes, and tears it down on unmount (the
 * element owns its WebSocket + MediaSource + reconnect lifecycle).
 */
export function MsePlayer({ src, className, muted = true }: Props) {
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

  // Apply mute to the underlying <video>. The element starts muted (so autoplay
  // works); when audio is wanted we poll briefly to keep it unmuted across the
  // video being (re)created on connect/reconnect. Muted tiles need no polling.
  useEffect(() => {
    const el = elRef.current;
    if (!el) return;
    const apply = () => {
      if (el.video) el.video.muted = muted;
    };
    apply();
    if (muted) return;
    const id = window.setInterval(apply, 1000);
    return () => window.clearInterval(id);
  }, [muted]);

  return <div ref={hostRef} className={className} />;
}
