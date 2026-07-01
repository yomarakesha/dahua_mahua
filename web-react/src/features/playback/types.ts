// ── Client → server control messages ──────────────────────────────────────────

export type SeekMsg      = { seek: number };           // footage epoch (UTC seconds)
export type SpeedMsg     = { speed: 1 | 2 | 4 | 8 };
export type PauseMsg     = { pause: true };
export type PlayMsg      = { play: true };
export type StreamMsg    = { stream: "main" };         // always "main" (no sub recorded; Contract #5)
export type KeepaliveMsg = { keepalive: true };

export type ClientMsg = SeekMsg | SpeedMsg | PauseMsg | PlayMsg | StreamMsg | KeepaliveMsg;

// ── Server → client typed JSON signals ────────────────────────────────────────
// Binary fMP4 fragments are NOT represented here (handled as ArrayBuffer in ws.onmessage).

export interface InitMsg {
  type: "init";
  t0: number;        // footage epoch of the first frame in this segment
  codec: string;     // full MIME for addSourceBuffer, e.g. 'video/mp4; codecs="avc1.640032"'
  // NOTE: no `audio` field — the backend drops audio server-side (`-an`, Contract #10),
  // so the init MIME is always video-only and no audio track is ever present.
}

export interface ReinitMsg {
  type: "reinit";
  t0: number;        // footage epoch of the first frame after seek/speed change
}

/**
 * Heartbeat clock signal from the backend.
 * Contract #3: wall_ts = current footage epoch (UTC seconds).
 * The client sets playhead = wall_ts directly for drift correction.
 * Field name is legacy; semantics = footage epoch (backend sends sess.footage_now()).
 */
export interface ClockMsg {
  type: "clock";
  wall_ts: number;   // current footage epoch (UTC seconds) — client: playhead = wall_ts
}

export interface EofMsg {
  type: "eof";
}

export interface ErrorMsg {
  type: "error";
  reason: string;    // sanitized human-readable message
}

export type ServerMsg = InitMsg | ReinitMsg | ClockMsg | EofMsg | ErrorMsg;

// ── Player state machine ───────────────────────────────────────────────────────

export type PlayerState =
  | "loading"       // WS open, waiting for first init + fMP4 data
  | "playing"       // video.play() active, media advancing
  | "paused"        // user-requested pause (video.pause() called)
  | "seeking"       // seek sent, waiting for reinit
  | "end"           // eof received and confirmed (no more clips)
  | "no_coverage"   // seek target has no clips for the day (WS never opened; see Contract #6)
  | "error";        // unrecoverable (NVR error, MSE append error, WS closed unexpectedly)

// State-machine transitions:
//   loading    → playing:      first fMP4 chunk appended + video.play() succeeds
//   playing    → paused:       user sends {pause}
//   paused     → playing:      user sends {play}
//   playing    → seeking:      user commits a seek (drag release or skip)
//   seeking    → loading:      reinit received (MSE rebuild started)
//   loading    → playing:      (same as above; re-enters via reinit)
//   playing    → end:          eof received after the last clip
//   (no_coverage is owned by PlaybackPage — the WS is never opened; see Contract #6)
//   any        → error:        {type:"error"} received OR MSE QuotaExceeded unrecoverable
//                               OR WS closed without eof (and not user-paused/seeking)

// ── Footage-time mapping ───────────────────────────────────────────────────────
// Playhead = t0 + (video.currentTime - baseCt)
// where t0 and baseCt are captured at each init/reinit.
// The clock heartbeat corrects drift: playhead = clockMsg.wall_ts (Contract #3).
// Speed is applied server-side; <video>.playbackRate stays 1.0 (Contract #13).

export interface FootageAnchor {
  t0: number;          // footage epoch captured from init/reinit
  baseCt: number;      // video.currentTime at the moment t0 was captured
  speed: 1 | 2 | 4 | 8;
}
