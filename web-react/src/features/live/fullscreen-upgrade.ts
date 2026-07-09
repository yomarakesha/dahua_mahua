/**
 * Instant-fullscreen upgrade state machine (pure — unit-tested in isolation).
 *
 * Fullscreen shows the SUB instantly (a warm producer opens it in ~0.5s), then
 * upgrades to the 4MP MAIN in the background and cross-fades once the main has
 * decoded its first frame (~2s, made invisible). This module decides which
 * layers render and whether the main layer is opaque, given the camera's stream
 * availability and the live-upgrade progress.
 */

/** Cross-fade duration (ms) for the main fading in over the sub. Kept short so
 *  a main that comes up almost as fast as the sub still cross-fades cleanly
 *  rather than snapping. Mirror this in the Tailwind `duration-*` on the layer. */
export const FADE_MS = 300;
/** Extra grace after the fade completes before the sub socket is torn down. */
export const TEAR_GRACE_MS = 80;

export interface FsLayers {
  /** Render the SUB player (low-res, instant). */
  showSub: boolean;
  /** Render the MAIN player (hi-res, upgrades in the background). */
  showMain: boolean;
  /** MAIN layer at full opacity (covering the sub). False = transparent, so the
   *  sub shows through underneath while the main warms up. */
  mainOpaque: boolean;
}

/**
 * @param hasSub   camera has a sub stream
 * @param hasMain  camera has a main stream
 * @param mainLive the main player has reached "live" at least once for this cam
 * @param subTorn  the post-fade grace has elapsed → sub socket freed
 */
export function fullscreenLayers(
  hasSub: boolean,
  hasMain: boolean,
  mainLive: boolean,
  subTorn: boolean,
): FsLayers {
  const showMain = hasMain;
  // Keep the sub until the main has fully taken over (live + fade done → torn).
  // A camera with no main (!hasMain) keeps the sub forever (no upgrade).
  const showSub = hasSub && !(hasMain && subTorn);
  // The main is visible when there is no sub to show under it (behave as today —
  // main only), or once the main has gone live and we cross-fade it in.
  const mainOpaque = showMain && (!hasSub || mainLive);
  return { showSub, showMain, mainOpaque };
}
