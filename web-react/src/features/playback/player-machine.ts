/**
 * Pure state machine for the VOD PlaybackPlayer.
 *
 * Extracted as a side-effect-free reducer so every transition is unit-testable
 * (jsdom has no real MediaSource/WebSocket). The component dispatches events
 * derived from BACKEND SIGNALS (init/reinit/clock/eof/gap/error) and USER
 * ACTIONS (pause/play/seek/speed) — never from "video.currentTime stopped"
 * (task-14 brief / Contract #3).
 */
import type { PlayerState } from "./types";

export const INITIAL_PLAYER_STATE: PlayerState = "loading";

export type PlayerEvent =
  /** Backend `init` — (re)building MediaSource+SourceBuffer, awaiting first data. */
  | { type: "init" }
  /** Backend `reinit` (after seek/speed) — rebuilding SourceBuffer, awaiting data. */
  | { type: "reinit" }
  /** First fMP4 chunk appended after (re)init + video.play() (loading → playing). */
  | { type: "playing" }
  /** User pressed pause. */
  | { type: "pause" }
  /** User pressed play. */
  | { type: "play" }
  /** User committed a seek OR changed speed (both await a backend reinit). */
  | { type: "seek" }
  /** Backend `gap`: next!=null → auto-skip seek; next===null → end of recording. */
  | { type: "gap"; next: number | null }
  /** Backend `eof` — end of the last clip. */
  | { type: "eof" }
  /** Backend `{type:"error"}`. */
  | { type: "error" }
  /** MSE QuotaExceeded survived a trim + single retry (Contract C2). */
  | { type: "quota_failed" }
  /** WebSocket closed (usePlaybackSession only fires onClose for UNEXPECTED closes). */
  | { type: "ws_close" }
  /** Parent (PlaybackPage) declared no coverage; player accepts being told (Contract #6). */
  | { type: "no_coverage" }
  /**
   * Explicit user-triggered reconnect (Retry after error). Tears down the dead
   * socket via reconnectNonce bump and opens a fresh WS. Goes directly to "loading"
   * (not "seeking") because the fresh session sends {seek} on open and we await
   * the backend's "init" signal — the same path as initial mount.
   */
  | { type: "reconnect" };

export function playerReducer(state: PlayerState, event: PlayerEvent): PlayerState {
  // "error" is terminal EXCEPT explicit recovery paths. Ignoring all other signals
  // stops a late eof/gap/ws_close from silently un-sticking the error overlay.
  if (state === "error") {
    switch (event.type) {
      case "reconnect":
        // Fresh-WS retry: go straight to loading (fresh session sends seek on open).
        return "loading";
      case "seek":
        // Seek over existing socket (optimistic; may be dead — prefer reconnect).
        return "seeking";
      case "init":
      case "reinit":
        return "loading";
      default:
        return "error";
    }
  }

  switch (event.type) {
    case "init":
    case "reinit":
      // (Re)building MSE — wait for the first data append to flip to "playing".
      return "loading";

    case "playing":
      // Only valid coming out of "loading": backend may keep streaming data while
      // the user has paused or after eof — never resurrect a paused/terminal state.
      return state === "loading" ? "playing" : state;

    case "pause":
      return state === "playing" ? "paused" : state;

    case "play":
      return state === "paused" ? "playing" : state;

    case "seek":
      // Explicit user seek (or speed change). Always valid — also the recovery path
      // out of "end"/"error" ("reconnect = explicit user seek", never an auto-loop).
      return "seeking";

    case "gap":
      // next!=null → snap to the next clip (auto-skip); next===null → end of recording.
      return event.next !== null ? "seeking" : "end";

    case "eof":
      return "end";

    case "error":
    case "quota_failed":
      return "error";

    case "ws_close":
      // Unexpected socket close → error, UNLESS we already finished cleanly
      // (a backend close right after eof must not clobber the "end" state).
      // The "error" state is already handled by the early return above.
      return state === "end" || state === "no_coverage" ? state : "error";

    case "no_coverage":
      return "no_coverage";

    case "reconnect":
      // Explicit Retry from any non-error state (e.g. double-click) → loading.
      return "loading";

    default:
      return state;
  }
}
