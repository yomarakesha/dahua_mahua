import { useEffect, useRef, useState } from "react";
import { CONFIG } from "@/lib/config";
import { registerDssMse } from "./dss-mse";
import type { VideoRTC } from "@/lib/vendor/video-rtc.js";

registerDssMse();

export type PlayerStatus = "connecting" | "live" | "error";
type Status = PlayerStatus;

interface Props {
  /** go2rtc stream name, e.g. `nvr-…_ch3` (sub) or `nvr-…_ch3_main`. */
  src: string;
  className?: string;
  /** Audio mute. Defaults true (grid tiles are always muted; fullscreen can
   *  unmute on a user gesture). */
  muted?: boolean;
  /**
   * Transport. "mse" (default) buffers and plays every frame in order — great for
   * the grid (subs have huge margin), but on a marginal 4MP main it thrashes and
   * freezes. "webrtc" is real-time and DROPS late frames instead of stalling — the
   * old-design behavior that kept the 4MP main smooth. Used for fullscreen mains.
   */
  mode?: "mse" | "webrtc";
  /** Notified when the stream status changes (so the parent can reflect it, e.g.
   *  hide a "LIVE" badge when the feed is lost). */
  onStatus?: (status: Status) => void;
}

/**
 * Video tile wrapping the <dss-mse> web component: mounts it once, re-points
 * `.src` when the stream changes, and tears it down on unmount (the element owns
 * its WebSocket + MediaSource/PeerConnection + reconnect lifecycle).
 */
export function MsePlayer({ src, className, muted = true, mode = "mse", onStatus }: Props) {
  const hostRef = useRef<HTMLDivElement>(null);
  const elRef = useRef<VideoRTC | null>(null);
  const firstSrcRef = useRef(true);
  const [status, setStatus] = useState<Status>("connecting");
  useEffect(() => {
    onStatus?.(status);
  }, [status, onStatus]);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const el = document.createElement("dss-mse") as VideoRTC;
    el.mode = mode;
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
    setStatus("connecting");
    el.src = new URL(`${CONFIG.go2rtcWsBase}/api/ws?src=${encodeURIComponent(src)}`);
  }, [src]);

  // Status overlay — a purely additive OBSERVER of the <video> (never touches the
  // connection). Drives the connecting-spinner / "signal lost" badge so the wall
  // shows which feeds are down instead of a frozen frame.
  useEffect(() => {
    const el = elRef.current;
    if (!el) return;
    let lastCt = -1;
    let stall = 0;
    let played = false;
    let connectTicks = 0;
    const id = window.setInterval(() => {
      const v = el.video;
      if (!v) return;
      if (v.error) {
        setStatus("error");
        return;
      }
      const advancing = v.currentTime !== lastCt && v.readyState >= 2;
      lastCt = v.currentTime;
      if (advancing) {
        stall = 0;
        played = true;
        setStatus("live");
      } else if (played) {
        // stalled mid-stream → signal lost after ~4.5s
        if (++stall >= 3) setStatus("error");
      } else {
        // never connected → give up the spinner after ~15s
        if (++connectTicks >= 10) setStatus("error");
      }
    }, 1500);
    return () => window.clearInterval(id);
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

  // Diagnostic logger. Enable with `localStorage.dssDebug = "1"` then reload.
  // Each second logs the buffer health so a freeze is attributable:
  //   ctΔ   ≈1.0 playing | 0 STALLED | >1 caught-up/jumped forward
  //   endΔ  ≈1.0 source delivering | 0 SOURCE STALLED (no data arriving)
  // → ctΔ=0 & endΔ=0 = source/network stall (nothing arriving)
  // → ctΔ=0 & endΔ>0 = data arrives but player won't advance (decode/MSE)
  // → ctΔ big jump    = re-center / latency catch-up
  //   gap = seconds behind live (buffer depth);  drop = decoder-dropped frames
  useEffect(() => {
    let on = false;
    try { on = localStorage.getItem("dssDebug") === "1"; } catch { /* ignore */ }
    if (!on) return;
    const el = elRef.current;
    if (!el) return;
    let lastCt = 0;
    let lastEnd = 0;
    let lastDrop = 0;
    const id = window.setInterval(() => {
      const v = el.video;
      if (!v) return;
      const b = v.buffered;
      const end = b.length ? b.end(b.length - 1) : 0;
      const ct = v.currentTime;
      const q = v.getVideoPlaybackQuality?.();
      const drop = q ? q.droppedVideoFrames : 0;
      const tot = q ? q.totalVideoFrames : 0;
      // eslint-disable-next-line no-console
      console.log(
        `[dss-buf ${src}] ct=${ct.toFixed(1)} end=${end.toFixed(1)} ` +
        `gap=${(end - ct).toFixed(1)}s rate=${v.playbackRate.toFixed(2)} ` +
        `ctΔ=${(ct - lastCt).toFixed(2)} endΔ=${(end - lastEnd).toFixed(2)} ` +
        `rs=${v.readyState} drop=${drop}(+${drop - lastDrop})/${tot}`,
      );
      lastCt = ct;
      lastEnd = end;
      lastDrop = drop;
    }, 1000);
    return () => window.clearInterval(id);
  }, [src]);

  return (
    <div className={`${className ?? ""} overflow-hidden`}>
      <div ref={hostRef} className="absolute inset-0" />
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
