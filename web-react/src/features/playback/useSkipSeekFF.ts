import { useEffect, useRef } from "react";

/**
 * Client-side fast-forward by SKIP-SEEKING.
 *
 * This NVR streams recorded footage over RTSP at ~realtime, so you physically
 * cannot render footage faster than it arrives — true Nx smooth playback is
 * impossible (and the old server-side frame-decimation just produced a black
 * screen; see session.py). Instead, at speed>1 we keep the backend playing the
 * normal, working 1x stream and periodically SEEK the footage forward so it
 * *samples* ahead at Nx: every ~STEP the playhead is advanced by
 * `speed × elapsed`, covering Nx the footage per wall-second. Each seek respawns
 * ffmpeg at the new position, so it looks stepped/choppy (a short clip, jump, a
 * short clip) — the honest best a realtime-only recorder allows.
 *
 * Anchored to wall-clock (not the reported playhead) so it advances at a steady
 * Nx regardless of per-seek respawn latency. Stops (via `onReachedEnd`) when it
 * catches up to the live edge / end of the day's recordings.
 */

/** Wall-clock between skip-seeks. ~2.5s amortises the ffmpeg respawn latency so
 *  each sampled window still shows a beat of real video before the next jump. */
const STEP_MS = 2500;

interface Args {
  /** User-facing speed: 1 (off) or 2/4/8 (skip-seek fast-forward). */
  speed: number;
  /** Latest footage-time playhead (epoch seconds) from the player. */
  playheadEpoch: number | null;
  /** End of the day's recording window (epoch seconds) — the forward cap. */
  dayEndEpoch: number;
  /** Debounced page-level seek (same commitSeek the timeline uses). */
  onSeek: (epoch: number) => void;
  /** Called when FF reaches the live edge / day end, so the page drops to 1x. */
  onReachedEnd: () => void;
}

export function useSkipSeekFF({ speed, playheadEpoch, dayEndEpoch, onSeek, onReachedEnd }: Args) {
  // Read the latest playhead without re-arming the interval every tick.
  const playheadRef = useRef(playheadEpoch);
  playheadRef.current = playheadEpoch;
  const onSeekRef = useRef(onSeek);
  onSeekRef.current = onSeek;
  const onEndRef = useRef(onReachedEnd);
  onEndRef.current = onReachedEnd;

  useEffect(() => {
    if (speed <= 1) return; // 1x = normal playback, no skip-seek
    const anchorFootage = playheadRef.current;
    if (anchorFootage == null) return; // wait until the player reports a position
    const anchorWall = performance.now();

    const id = window.setInterval(() => {
      const elapsedSec = (performance.now() - anchorWall) / 1000;
      const now = Math.floor(Date.now() / 1000);
      const cap = Math.min(dayEndEpoch, now - 1); // never seek to/after live
      const target = Math.floor(anchorFootage + speed * elapsedSec);
      if (target >= cap) {
        onSeekRef.current(cap);
        onEndRef.current(); // caught up to the edge → parent drops to 1x
        return;
      }
      onSeekRef.current(target);
    }, STEP_MS);

    return () => window.clearInterval(id);
    // Re-anchor whenever the speed changes (2→4→8) or the day window changes.
  }, [speed, dayEndEpoch]);
}
