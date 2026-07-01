/**
 * PlaybackPlayer — purpose-built VOD MSE player driven by backend WS signals.
 *
 * Owns its own MediaSource + a single persistent WebSocket (via usePlaybackSession).
 * It deliberately does NOT reuse dss-mse / VideoRTC / WebCodecsEngine — only the
 * `ondata → appendBuffer` one-pending-buffer queue pattern from video-rtc.js.
 *
 * Invariants (task-14 brief / binding contracts):
 *  - <video>.playbackRate stays 1.0 ALWAYS (Contract #13). Speed is backend-owned
 *    (we send {speed} and the server remaps footage time); audio muted when speed>1.
 *  - State machine is driven by BACKEND SIGNALS + USER ACTIONS via the pure
 *    playerReducer — never "currentTime stopped".
 *  - No live-edge tricks: no setLiveSeekableRange, no currentTime re-centering, no
 *    playbackRate nudging, no auto-reconnect.
 *  - QuotaExceeded → trim ranges older than currentTime-30s, ONE retry, else error.
 *
 * jsdom note: MediaSource is feature-detected so the module imports cleanly under
 * the test runner. Real MSE/WS behavior is covered by the DEFERRED Playwright
 * checklist in the task report, not unit tests.
 */
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { footageEpoch } from "./playback-utils";
import { playerReducer, INITIAL_PLAYER_STATE } from "./player-machine";
import { usePlaybackSession } from "./usePlaybackSession";
import type { PlaybackSessionOptions } from "./usePlaybackSession";
import type { FootageAnchor, PlayerState, ServerMsg } from "./types";

type Speed = 1 | 2 | 4 | 8;

/** Seconds of buffer to keep behind currentTime when trimming on QuotaExceeded. */
const TRIM_KEEP_SECONDS = 30;

/** Read a SourceBuffer's buffered span SAFELY. Returns null when the SB has no
 *  ranges OR has been detached from its MediaSource (rebuild on seek/reinit) —
 *  reading `.buffered` on a detached SB throws InvalidStateError, which if
 *  uncaught crashes the player and blanks the video. */
function bufferedRange(sb: SourceBuffer | null): { start: number; end: number } | null {
  try {
    if (!sb || sb.buffered.length === 0) return null;
    return { start: sb.buffered.start(0), end: sb.buffered.end(sb.buffered.length - 1) };
  } catch {
    return null; // SourceBuffer detached from its parent MediaSource
  }
}

export interface PlaybackPlayerProps {
  nvrId: string;
  channel: number;
  /** Footage epoch to start / seek to. When it changes the player sends {seek}. */
  seekTarget: number | null;
  /** Playback speed (backend-owned). When changed the player sends {speed}. */
  speed: Speed;
  /**
   * RTSP transport for this session: "udp" (Smooth, default; near-realtime
   * but lossy on this NVR) or "tcp" (Clear; clean but slow). Changing it
   * reopens the WS with the new query param (Contract #10) — same
   * teardown/reconnect path as the reconnectNonce Retry mechanism.
   */
  transport?: "udp" | "tcp";
  /** Optional external ref to the <video> (for Task 15 snapshot). */
  videoRef?: React.RefObject<HTMLVideoElement>;
  /** Notifies parent of state-machine changes (snapshot enable, overlays, …). */
  onStateChange?: (state: PlayerState) => void;
  /** Current footage-time playhead (epoch seconds) for the Timeline. */
  onPlayhead?: (epoch: number) => void;
  /** Latest FootageAnchor (for Task 15 snapshot footage-time mapping). */
  onAnchorChange?: (anchor: FootageAnchor | null) => void;
  /** Fired the first time playback reaches "playing". */
  onReady?: () => void;
}

const hasMediaSource = typeof window !== "undefined" && "MediaSource" in window;

export default function PlaybackPlayer({
  nvrId,
  channel,
  seekTarget,
  speed,
  transport = "udp",
  videoRef: externalVideoRef,
  onStateChange,
  onPlayhead,
  onAnchorChange,
  onReady,
}: PlaybackPlayerProps) {
  const internalVideoRef = useRef<HTMLVideoElement | null>(null);
  const videoRef = externalVideoRef ?? internalVideoRef;

  const [state, dispatch] = useReducer(playerReducer, INITIAL_PLAYER_STATE);

  // ── Reconnect nonce — increment to force a fresh WS session (Retry) ─────────────
  // When bumped, usePlaybackSession tears down the dead socket and opens a new one
  // that sends {seek: currentSeekTarget} on open. Dispatch "reconnect" in the same
  // handler so the reducer immediately exits "error" → "loading" rather than staying
  // stuck in the error overlay while the new WS handshakes.
  const [reconnectNonce, setReconnectNonce] = useState(0);

  // ── MSE refs ──────────────────────────────────────────────────────────────────
  const msRef = useRef<MediaSource | null>(null);
  const sbRef = useRef<SourceBuffer | null>(null);
  const objectUrlRef = useRef<string | null>(null);
  const codecRef = useRef<string>("");
  /** FIFO of fMP4 chunks awaiting appendBuffer. A queue (not one-pending) because
   *  data can arrive BEFORE `sourceopen` — dropping it loses the init segment and
   *  nothing decodes. For VOD we never drop mid-stream either (server back-pressures). */
  const queueRef = useRef<ArrayBuffer[]>([]);
  /** Whether a QuotaExceeded trim+retry is already in flight (Contract C2: single retry). */
  const quotaRetryRef = useRef(false);
  /** True until the first chunk is appended after an init/reinit (loading→playing). */
  const firstAppendRef = useRef(false);

  // ── Footage anchor ──────────────────────────────────────────────────────────────
  const anchorRef = useRef<FootageAnchor | null>(null);
  const speedRef = useRef<Speed>(speed);
  speedRef.current = speed;
  /** Pending anchor from the last init/reinit — {t0, speed} only. baseCt is captured
   *  in onAppendSuccess AFTER currentTime settles to the real buffered start, because
   *  reading video.currentTime during init/reinit (before the src reset settles) is
   *  stale and would mis-map every footage-time (MED-6). */
  const pendingAnchorRef = useRef<{ t0: number; speed: Speed } | null>(null);

  const setAnchor = useCallback(
    (a: FootageAnchor | null) => {
      anchorRef.current = a;
      onAnchorChange?.(a);
    },
    [onAnchorChange],
  );

  // ── Notify parent on state change + emit onReady once ───────────────────────────
  const readyFiredRef = useRef(false);
  useEffect(() => {
    onStateChange?.(state);
    if (state === "playing" && !readyFiredRef.current) {
      readyFiredRef.current = true;
      onReady?.();
    }
  }, [state, onStateChange, onReady]);

  // ── MSE append: a FIFO queue, drained once the SourceBuffer is ready ─────────────
  /** Safety cap so a wedged SourceBuffer can't OOM the tab (server bounds this). */
  const MAX_QUEUE = 600;

  const onAppendSuccess = useCallback(() => {
    quotaRetryRef.current = false;
    const video = videoRef.current;
    const r = bufferedRange(sbRef.current);
    // Keep the playhead inside the buffered range. A fragmented MP4 whose first
    // fragment has a non-zero baseMediaDecodeTime leaves currentTime=0 OUTSIDE
    // [start,end], so the decoder renders nothing (black) even while "playing".
    if (video && r) {
      if (video.currentTime < r.start || video.currentTime > r.end) {
        try {
          video.currentTime = r.start;
        } catch {
          /* not seekable yet — a later append will retry */
        }
      }
    }
    if (firstAppendRef.current) {
      firstAppendRef.current = false;
      // Capture the anchor NOW — currentTime has settled to the real buffered start
      // (set just above), so baseCt matches the frame t0 actually maps to (MED-6).
      const pending = pendingAnchorRef.current;
      if (pending) {
        setAnchor({ t0: pending.t0, baseCt: video?.currentTime ?? 0, speed: pending.speed });
        pendingAnchorRef.current = null;
      }
      dispatch({ type: "playing" });
      // Start playback now that media is present. The element is muted, so the
      // browser allows autoplay (unmuted autoplay from this async WS handler is
      // blocked → black video with a false "playing" state).
      void video?.play().catch(() => {});
    }
  }, [videoRef, setAnchor]);

  /** Trim buffered ranges older than currentTime - 30 s (async; updateend retries). */
  const trimBuffer = useCallback(() => {
    const sb = sbRef.current;
    const video = videoRef.current;
    if (!sb || !video) return false;
    try {
      if (sb.buffered.length > 0) {
        const start = sb.buffered.start(0);
        const cutoff = video.currentTime - TRIM_KEEP_SECONDS;
        if (cutoff > start) {
          sb.remove(start, cutoff); // async → updateend drains the queue (the retry)
          return true;
        }
      }
    } catch {
      /* SB detached mid-flight — fall through to "couldn't trim". */
    }
    return false;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleAppendError = useCallback(
    (e: unknown, data: ArrayBuffer) => {
      const isQuota = e instanceof DOMException && e.name === "QuotaExceededError";
      if (!isQuota) {
        dispatch({ type: "error" });
        return;
      }
      if (quotaRetryRef.current) {
        // Already trimmed + retried once and still over quota (Contract C2) → give up.
        quotaRetryRef.current = false;
        dispatch({ type: "quota_failed" });
        return;
      }
      quotaRetryRef.current = true;
      queueRef.current.unshift(data); // put it back at the front; retried after the trim
      if (!trimBuffer()) {
        // Nothing to trim → the single retry can't help.
        quotaRetryRef.current = false;
        queueRef.current.shift();
        dispatch({ type: "quota_failed" });
      }
    },
    [trimBuffer],
  );

  /** Append the next queued chunk if the SourceBuffer is ready and idle. */
  const drainQueue = useCallback(() => {
    const sb = sbRef.current;
    const ms = msRef.current;
    if (!sb || !ms || ms.readyState !== "open" || sb.updating) return;
    const data = queueRef.current.shift();
    if (data === undefined) return;
    try {
      sb.appendBuffer(data);
      onAppendSuccess();
    } catch (e) {
      handleAppendError(e, data);
    }
  }, [onAppendSuccess, handleAppendError]);

  const onUpdateEnd = useCallback(() => {
    drainQueue();
  }, [drainQueue]);

  const appendData = useCallback(
    (data: ArrayBuffer) => {
      const q = queueRef.current;
      q.push(data);
      if (q.length > MAX_QUEUE) q.shift(); // defensive; never hit in normal operation
      drainQueue();
    },
    [drainQueue],
  );

  // ── MSE (re)build — on each init/reinit ──────────────────────────────────────────
  const rebuildMse = useCallback(
    (codec: string) => {
      const video = videoRef.current;
      if (!hasMediaSource || !video) return;

      // Tear down any previous MediaSource/object URL.
      if (objectUrlRef.current) {
        URL.revokeObjectURL(objectUrlRef.current);
        objectUrlRef.current = null;
      }
      sbRef.current = null;
      queueRef.current = [];
      quotaRetryRef.current = false;
      firstAppendRef.current = true;
      codecRef.current = codec;

      const ms = new MediaSource();
      msRef.current = ms;
      const url = URL.createObjectURL(ms);
      objectUrlRef.current = url;
      video.src = url;
      video.playbackRate = 1.0; // invariant guard (Contract #13)

      ms.addEventListener(
        "sourceopen",
        () => {
          try {
            const sb = ms.addSourceBuffer(codec);
            sb.mode = "segments";
            sb.addEventListener("updateend", onUpdateEnd);
            sbRef.current = sb;
            drainQueue(); // flush anything that arrived before sourceopen (init segment!)
          } catch {
            dispatch({ type: "error" }); // wrong MIME / unsupported codec
          }
        },
        { once: true },
      );
    },
    [onUpdateEnd, drainQueue, videoRef],
  );

  // ── Signal handling ──────────────────────────────────────────────────────────────
  const handleSignal = useCallback(
    (msg: ServerMsg) => {
      const video = videoRef.current;
      switch (msg.type) {
        case "init": {
          rebuildMse(msg.codec);
          // baseCt is captured in onAppendSuccess after currentTime settles (MED-6).
          pendingAnchorRef.current = { t0: msg.t0, speed: speedRef.current };
          setAnchor(null); // clear stale anchor until the first append re-captures it
          dispatch({ type: "init" });
          void video?.play().catch(() => {}); // autoplay may be blocked; ignore
          break;
        }
        case "reinit": {
          rebuildMse(codecRef.current); // reinit reuses the last init's codec
          pendingAnchorRef.current = { t0: msg.t0, speed: speedRef.current };
          setAnchor(null); // captured in onAppendSuccess once currentTime settles (MED-6)
          dispatch({ type: "reinit" });
          void video?.play().catch(() => {});
          break;
        }
        case "clock": {
          // Contract #3: wall_ts IS the current footage epoch. Re-anchor
          // (t0=wall_ts, baseCt=currentTime) so accumulated drift is corrected, but
          // do NOT jump the playhead here — the RAF loop derives it from
          // footageEpoch(anchor, currentTime), avoiding a per-tick forward jump (MED-6).
          setAnchor({
            t0: msg.wall_ts,
            baseCt: video?.currentTime ?? 0,
            speed: speedRef.current,
          });
          break;
        }
        case "eof":
          dispatch({ type: "eof" });
          break;
        case "error":
          dispatch({ type: "error" });
          break;
        default:
          // {stream} is a client-side main-only no-op (Contract #5); the server
          // never emits it, so there is nothing to handle here.
          break;
      }
    },
    [rebuildMse, setAnchor, videoRef],
  );

  const handleClose = useCallback(() => {
    dispatch({ type: "ws_close" });
  }, []);

  // ── WebSocket session ──────────────────────────────────────────────────────────
  // reconnectNonce is the ONLY explicit reconnect trigger — it tears down the old
  // socket and opens a fresh one. Normal seek/speed changes go via send({seek/speed})
  // over the existing socket. seekTarget is included in opts (and memo deps) so that
  // optsRef.current.initialSeek is always fresh: when the fresh WS opens on reconnect,
  // ws.onopen reads optsRef.current.initialSeek and seeks to the current position.
  // seekTarget changes alone do NOT reconnect — only reconnectNonce does (the hook's
  // effect dep is [enabled, nvrId, channel, reconnectNonce]).
  const sessionOpts: PlaybackSessionOptions | null = useMemo(
    () => ({
      nvrId,
      channel,
      initialSeek: seekTarget ?? 0,
      transport,
      reconnectNonce,
      onSignal: handleSignal,
      onData: appendData,
      onClose: handleClose,
    }),
    // seekTarget included so optsRef.current.initialSeek stays current for reconnects.
    // reconnectNonce included so a Retry reopens the socket (hook's own dep).
    // transport included so a Smooth/Clear toggle reopens the socket (the hook's own
    // effect deps include `transport`, mirroring the reconnectNonce teardown path).
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [nvrId, channel, seekTarget, transport, reconnectNonce, handleSignal, appendData, handleClose],
  );
  const session = usePlaybackSession(sessionOpts);
  const sessionRef = useRef(session);
  sessionRef.current = session;

  // ── Seek prop changes → send {seek} (skip the mount value: that's initialSeek) ──
  const seekMountedRef = useRef(false);
  useEffect(() => {
    if (!seekMountedRef.current) {
      seekMountedRef.current = true;
      return;
    }
    if (seekTarget != null && sessionRef.current) {
      sessionRef.current.send({ seek: seekTarget });
      dispatch({ type: "seek" });
    }
  }, [seekTarget]);

  // ── Speed prop changes → send {speed}, re-await reinit ──────────────────────────
  const speedMountedRef = useRef(false);
  useEffect(() => {
    if (!speedMountedRef.current) {
      speedMountedRef.current = true;
      return;
    }
    if (sessionRef.current) {
      sessionRef.current.send({ speed });
      setAnchor(null); // refreshed by the upcoming reinit
      dispatch({ type: "seek" }); // speed change awaits a backend reinit (→ seeking)
    }
  }, [speed, setAnchor]);

  // ── Transport prop changes → the WS itself is torn down + reopened by
  // usePlaybackSession's own effect deps (transport is one of them); this effect
  // only drives the UI feedback, mirroring the speed-change effect above: show the
  // seeking/loading overlay while the fresh socket handshakes and sends its {init}. ──
  const transportMountedRef = useRef(false);
  useEffect(() => {
    if (!transportMountedRef.current) {
      transportMountedRef.current = true;
      return;
    }
    setAnchor(null); // refreshed by the upcoming init on the fresh WS
    dispatch({ type: "seek" }); // transport toggle awaits a fresh WS + init (→ seeking)
  }, [transport, setAnchor]);

  // ── Muted by default so autoplay is always permitted ────────────────────────────
  // Browsers block UNMUTED autoplay from a non-gesture context (our WS init
  // handler) — it showed as a black frame with a false "playing" state. Most
  // recorded footage here has no audio anyway; keep muted (Contract #13/§4 also
  // requires muted at speed>1). Follow-up: an explicit unmute control.
  useEffect(() => {
    if (videoRef.current) videoRef.current.muted = true;
  }, [speed, videoRef]);

  // ── Surface <video> element errors (diagnostic only) ────────────────────────────
  // LOG the MediaError but do NOT drop to the error state: the <video> fires benign
  // 'error' events during normal operation — the MediaSource src is swapped on every
  // reinit/seek, and lossy 4MP decode hiccups also fire it. Hard-erroring here blanks
  // a stream that is otherwise decoding (regression 2026-07-01). Genuinely fatal
  // conditions surface via the append-error and WS-close paths instead.
  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    const onErr = () => {
      const err = v.error;
      // eslint-disable-next-line no-console
      console.warn("[Playback] <video> error (non-fatal, ignored)", err?.code, err?.message, {
        codec: codecRef.current,
        buffered: bufferedRange(sbRef.current) ? "present" : "none/detached",
        currentTime: v.currentTime,
      });
    };
    v.addEventListener("error", onErr);
    return () => v.removeEventListener("error", onErr);
  }, [videoRef]);

  // ── Playhead RAF loop while playing ───────────────────────────────────────────────
  useEffect(() => {
    if (state !== "playing") return;
    let raf = 0;
    const tick = () => {
      const video = videoRef.current;
      const anchor = anchorRef.current;
      if (video && anchor) onPlayhead?.(footageEpoch(anchor, video.currentTime));
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [state, onPlayhead, videoRef]);

  // ── Teardown MSE on unmount ─────────────────────────────────────────────────────
  useEffect(() => {
    return () => {
      if (objectUrlRef.current) {
        URL.revokeObjectURL(objectUrlRef.current);
        objectUrlRef.current = null;
      }
      msRef.current = null;
      sbRef.current = null;
      queueRef.current = [];
    };
  }, []);

  // ── User pause/play controls ──────────────────────────────────────────────────────
  const handlePause = useCallback(() => {
    sessionRef.current?.send({ pause: true });
    videoRef.current?.pause();
    dispatch({ type: "pause" });
  }, [videoRef]);

  const handlePlay = useCallback(() => {
    sessionRef.current?.send({ play: true });
    void videoRef.current?.play().catch(() => {});
    dispatch({ type: "play" });
  }, [videoRef]);

  // ── Render ────────────────────────────────────────────────────────────────────────
  const busy = state === "loading" || state === "seeking";

  return (
    <div className="absolute inset-0 flex items-center justify-center bg-black">
      <video
        ref={videoRef}
        className="h-full w-full object-contain"
        playsInline
        muted
        // controls intentionally omitted — controls are WS messages, not native UI
        data-player-state={state}
      />

      {/* ── Overlays ──────────────────────────────────────────────────────────── */}
      {busy && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center bg-black/30">
          <span className="flex items-center gap-2 font-mono text-3xs uppercase tracking-wider text-ink-faint">
            <span className="h-3 w-3 animate-spin rounded-full border border-ink-faint/50 border-t-transparent" />
            {state === "seeking" ? "seeking" : "loading"}
          </span>
        </div>
      )}

      {state === "paused" && (
        <button
          aria-label="Resume playback"
          onClick={handlePlay}
          className="absolute inset-0 flex items-center justify-center bg-black/30"
        >
          <span className="flex h-14 w-14 items-center justify-center rounded-full bg-white/10 ring-1 ring-white/20">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor" className="text-ink-soft">
              <path d="M8 5v14l11-7z" />
            </svg>
          </span>
        </button>
      )}

      {state === "end" && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center bg-black/40">
          <span className="rounded border border-white/15 bg-white/[.06] px-3 py-1.5 font-mono text-3xs uppercase tracking-wider text-ink-soft">
            End of recording
          </span>
        </div>
      )}

      {state === "error" && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-black/50">
          <span className="flex items-center gap-1.5 rounded border border-danger/40 bg-danger/[.14] px-2 py-1 font-mono text-3xs font-bold uppercase tracking-wider text-danger">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-danger" />
            playback error
          </span>
          <button
            onClick={() => {
              // Bump the nonce so usePlaybackSession tears down the dead socket and
              // opens a fresh WS (handles ws_close + all other error origins uniformly).
              // dispatch("reconnect") moves error→loading immediately so the spinner
              // shows while the new handshake completes — no stuck-in-seeking risk.
              setReconnectNonce((n) => n + 1);
              dispatch({ type: "reconnect" });
            }}
            className="rounded-md border border-white/10 bg-white/[.05] px-3 py-1 text-xs font-semibold text-ink-soft transition hover:bg-white/[.1]"
          >
            Retry
          </button>
        </div>
      )}

      {/* Pause control (visible while playing) */}
      {state === "playing" && (
        <button
          aria-label="Pause playback"
          onClick={handlePause}
          className="absolute bottom-3 left-3 flex h-9 w-9 items-center justify-center rounded-full bg-black/40 text-ink-soft ring-1 ring-white/10 transition hover:bg-black/60"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
            <path d="M6 5h4v14H6zM14 5h4v14h-4z" />
          </svg>
        </button>
      )}
    </div>
  );
}
