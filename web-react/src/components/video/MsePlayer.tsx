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
  const firstSrcRef = useRef(true);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const el = document.createElement("dss-mse") as VideoRTC;
    el.mode = "mse";
    el.background = true;
    host.appendChild(el);
    elRef.current = el;
    return () => {
      // Deterministically close the WebSocket + MediaSource. el.remove() alone
      // won't: background=true makes disconnectedCallback a no-op, and setting
      // src="" early-returns in onconnect without closing the socket — which
      // leaks a go2rtc consumer (and its RTSP pull) per unmount. ondisconnect()
      // closes ws + pc and clears the <video>.
      try {
        el.ondisconnect();
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
    // On a live element, tear down the old connection before re-pointing .src:
    // VideoRTC.onconnect() early-returns when a WebSocket already exists, so
    // without this a stream switch would keep playing the OLD stream.
    //
    // BUT skip it on first mount. ondisconnect() sets <video>.src='' (video-rtc
    // line ~341), which makes the browser fire an async MEDIA_ERR_SRC_NOT_SUPPORTED
    // ("Empty src") whose handler runs `this.ws.close()` — and by the time that
    // async error fires we've already opened the new socket, so it would close a
    // CONNECTING socket ("closed before the connection is established"). On first
    // mount there's nothing to tear down anyway. (video-rtc.js also now guards the
    // error handler against this synthetic error, for the stream-switch case.)
    if (!firstSrcRef.current) {
      try {
        el.ondisconnect();
      } catch {
        /* ignore */
      }
    }
    firstSrcRef.current = false;
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
