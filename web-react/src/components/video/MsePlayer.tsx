import { useCallback, useEffect, useRef, useState } from "react";
import { CONFIG } from "@/lib/config";
import { recordEvent } from "@/lib/diagnostics";
import { CameraOffIcon, RefreshIcon } from "@/components/icons";
import { registerDssMse } from "./dss-mse";
import type { VideoRTC } from "@/lib/vendor/video-rtc.js";

registerDssMse();

export type PlayerStatus = "connecting" | "live" | "error";
type Status = PlayerStatus;

/** Max self-heal reconnect attempts before we give up and show a manual Retry. */
const MAX_HEAL = 3;
/** Grace before a hidden tab tears its socket down — quick tab flips shouldn't churn. */
const HIDDEN_GRACE_MS = 10_000;

/**
 * Growing backoff (ms) for auto-heal reconnects: 1s → 2s → 4s (capped), plus up
 * to 1s of jitter so a wall of tiles healing at once doesn't slam go2rtc in
 * lockstep (mirrors the vendor onclose jitter). Exported for unit testing.
 */
export function healBackoffMs(attempt: number): number {
  const base = Math.min(4000, 1000 * 2 ** Math.max(0, attempt - 1));
  return base + Math.random() * 1000;
}

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
  /**
   * Delay (ms) before this tile opens its FIRST socket. The live wall uses a
   * per-tile stagger on page/patrol flips so N tiles don't slam go2rtc (N sockets
   * + N RTSP pulls) at the same instant. Only applied on the initial connect.
   */
  connectDelayMs?: number;
}

/**
 * Video tile wrapping the <dss-mse> web component: mounts it once, re-points
 * `.src` when the stream changes, and tears it down on unmount (the element owns
 * its WebSocket + MediaSource/PeerConnection + reconnect lifecycle).
 */
export function MsePlayer({
  src,
  className,
  muted = true,
  mode = "mse",
  onStatus,
  connectDelayMs = 0,
}: Props) {
  const hostRef = useRef<HTMLDivElement>(null);
  const elRef = useRef<VideoRTC | null>(null);
  const firstSrcRef = useRef(true);
  // The WebSocket URL for the current src — used by the auto-heal (#1), the
  // manual Retry (#2), and the hidden→visible reconnect (#5), all of which need
  // to re-point `.src` without going through the src-prop effect.
  const wsUrlRef = useRef<URL | null>(null);
  // Self-heal bookkeeping. `attempts` counts consecutive auto-heals since the
  // last time the stream was live; `healing` is true while a heal teardown is
  // mid-flight (socket down, waiting to re-open) — during which the poller must
  // NOT flip to error on the synthetic empty-src video error ondisconnect() causes.
  const healRef = useRef<{ attempts: number; healing: boolean; timer: number }>({
    attempts: 0,
    healing: false,
    timer: 0,
  });
  // Hidden-tab teardown state (#5): grace timer id + whether we tore down on hide.
  const hiddenRef = useRef<{ timer: number; torn: boolean }>({ timer: 0, torn: false });
  const [status, setStatus] = useState<Status>("connecting");
  useEffect(() => {
    onStatus?.(status);
  }, [status, onStatus]);

  // Re-point the element at wsUrlRef after a fresh teardown (heal / visible /
  // manual retry). ondisconnect() closes the old socket; a short backoff+jitter
  // avoids a synchronous reopen and spreads simultaneous reconnects.
  const reconnect = useCallback((backoffMs: number) => {
    const el = elRef.current;
    const url = wsUrlRef.current;
    if (!el || !url) return;
    const h = healRef.current;
    if (h.timer) window.clearTimeout(h.timer);
    try {
      el.ondisconnect();
    } catch {
      /* ignore */
    }
    setStatus("connecting");
    h.timer = window.setTimeout(() => {
      h.timer = 0;
      h.healing = false;
      const e = elRef.current;
      if (e) e.src = url;
    }, backoffMs);
  }, []);

  // Manual retry (#2): operator clicked Retry on a dead tile → reset the heal
  // budget and reconnect immediately.
  const handleRetry = useCallback(() => {
    healRef.current.attempts = 0;
    healRef.current.healing = false;
    recordEvent("live-heal", `${src} manual-retry`);
    reconnect(0);
  }, [reconnect, src]);

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
      if (healRef.current.timer) window.clearTimeout(healRef.current.timer);
      if (hiddenRef.current.timer) window.clearTimeout(hiddenRef.current.timer);
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
    const first = firstSrcRef.current;
    if (!first) {
      try {
        el.ondisconnect();
      } catch {
        /* ignore */
      }
    }
    firstSrcRef.current = false;
    // A new src invalidates any in-flight heal / hidden state for the old stream.
    healRef.current.attempts = 0;
    healRef.current.healing = false;
    hiddenRef.current.torn = false;
    setStatus("connecting");
    const url = new URL(`${CONFIG.go2rtcWsBase}/api/ws?src=${encodeURIComponent(src)}`);
    wsUrlRef.current = url;
    // #4: on the FIRST connect only, honour the caller's stagger so a page/patrol
    // flip doesn't open N sockets in the same tick. Later re-points (heal/visible)
    // never take this path.
    if (first && connectDelayMs > 0) {
      const t = window.setTimeout(() => {
        const e = elRef.current;
        if (e) e.src = url;
      }, connectDelayMs);
      return () => window.clearTimeout(t);
    }
    el.src = url;
  }, [src, connectDelayMs]);

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
    // #1: force a real reconnect. video-rtc only reconnects on WS close / video
    // error; when go2rtc keeps the socket OPEN but data stops, it never heals →
    // the tile shows "signal lost" forever. Tear down + re-point after backoff.
    const heal = () => {
      const h = healRef.current;
      h.attempts += 1;
      h.healing = true;
      recordEvent("live-heal", `${src} attempt ${h.attempts}/${MAX_HEAL}`);
      reconnect(healBackoffMs(h.attempts));
    };
    const id = window.setInterval(() => {
      const v = el.video;
      if (!v) return;
      const h = healRef.current;
      // A hidden tab is torn down on purpose (#5) — don't heal it; the visible
      // handler reconnects. Also skip while a heal teardown is mid-flight.
      if (document.hidden || h.healing) {
        lastCt = v.currentTime;
        return;
      }
      if (v.error) {
        setStatus("error");
        return;
      }
      const advancing = v.currentTime !== lastCt && v.readyState >= 2;
      lastCt = v.currentTime;
      if (advancing) {
        stall = 0;
        played = true;
        h.attempts = 0; // healthy again → refill the heal budget
        setStatus("live");
      } else if (played) {
        // stalled mid-stream (~4.5s). If the socket is still OPEN, go2rtc won't
        // reconnect on its own → self-heal (bounded); otherwise the vendor's own
        // close-driven reconnect is already running, so just surface the error.
        if (++stall >= 3) {
          const wsOpen = el.ws != null && el.ws.readyState === WebSocket.OPEN;
          if (wsOpen && h.attempts < MAX_HEAL) {
            stall = 0;
            heal();
          } else {
            setStatus("error");
          }
        }
      } else {
        // never connected → give up the spinner after ~15s
        if (++connectTicks >= 10) setStatus("error");
      }
    }, 1500);
    return () => window.clearInterval(id);
  }, [src, reconnect]);

  // #5: hidden-tab tiles keep decoding because background=true skips the vendor's
  // visibility handling. Tear the socket down after a grace period when the tab
  // goes hidden, and reconnect (staggered) when it returns. Guarded against the
  // stall-heal via hiddenRef/document.hidden checks in the poller.
  useEffect(() => {
    const onVis = () => {
      const el = elRef.current;
      if (!el) return;
      const hs = hiddenRef.current;
      if (document.hidden) {
        if (hs.timer) window.clearTimeout(hs.timer);
        hs.timer = window.setTimeout(() => {
          hs.timer = 0;
          hs.torn = true;
          healRef.current.healing = false;
          try {
            el.ondisconnect();
          } catch {
            /* ignore */
          }
          setStatus("connecting");
        }, HIDDEN_GRACE_MS);
      } else {
        if (hs.timer) {
          window.clearTimeout(hs.timer);
          hs.timer = 0;
        }
        if (hs.torn) {
          hs.torn = false;
          healRef.current.attempts = 0;
          // small per-tile jitter so a wall of tiles doesn't reconnect in lockstep
          reconnect(Math.random() * 800);
        }
      }
    };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      document.removeEventListener("visibilitychange", onVis);
      if (hiddenRef.current.timer) window.clearTimeout(hiddenRef.current.timer);
    };
  }, [reconnect]);

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
      {status === "connecting" && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center bg-black/30">
          <span className="flex items-center gap-2 font-mono text-3xs uppercase tracking-wider text-ink-faint">
            <span className="h-3 w-3 animate-spin rounded-full border border-ink-faint/50 border-t-transparent" />
            connecting
          </span>
        </div>
      )}
      {status === "error" && (
        // #2: dead tiles were a dead end (only "signal lost"). Give the operator a
        // camera-off glyph and a Retry that resets the heal budget + reconnects.
        // The container stays pointer-events-none so it never blocks the tile's
        // click/context-menu; only the Retry control is interactive.
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center gap-2 bg-black/45">
          <CameraOffIcon size={22} className="text-danger/80" />
          <span className="font-mono text-3xs font-bold uppercase tracking-wider text-danger">
            signal lost
          </span>
          <div
            role="button"
            tabIndex={0}
            aria-label="Retry stream"
            onClick={(e) => {
              e.stopPropagation();
              handleRetry();
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                e.stopPropagation();
                handleRetry();
              }
            }}
            className="pointer-events-auto flex h-8 min-w-[32px] cursor-pointer items-center gap-1.5 rounded-md border border-white/15 bg-white/[.06] px-2.5 font-mono text-3xs font-semibold uppercase tracking-wider text-ink-soft transition hover:bg-white/[.12] focus:outline-none focus-visible:ring-1 focus-visible:ring-accent/50"
          >
            <RefreshIcon size={13} />
            Retry
          </div>
        </div>
      )}
    </div>
  );
}
