/**
 * useSnapshot — client-side PNG snapshot from the current <video> frame.
 *
 * Draws the current video frame to an off-screen canvas, encodes it as PNG via
 * canvas.toBlob, and triggers a browser download.  The filename is derived from
 * the NVR-local footage timestamp so the user gets a meaningful, timezone-correct
 * filename regardless of their browser locale.
 *
 * jsdom / unit-test note:
 *   canvas.toBlob and URL.createObjectURL have no real implementation in jsdom;
 *   the takeSnapshot path is therefore exercised only in Playwright / manual tests.
 *   The pure logic helper (buildSnapshotFilename) is fully unit-tested in
 *   playback-utils.test.ts.
 *
 * DEFERRED manual checklist:
 *   - Play a short clip, click Snapshot.
 *   - Verify a PNG file downloads.
 *   - Open the PNG — confirm it shows the correct video frame.
 *   - Confirm the filename format is
 *     "snapshot_{camName}_{YYYY-MM-DD_HH-MM-SS}.png" in NVR-local time.
 */
import type React from "react";
import { footageEpoch, buildSnapshotFilename } from "./playback-utils";
import type { FootageAnchor } from "./types";

/**
 * Returns a `takeSnapshot` function.  When called:
 *   1. Draws videoEl.currentTime frame to a hidden canvas.
 *   2. canvas.toBlob("image/png") → download as
 *      "snapshot_{camDisplayName}_{NVR-local-datetime}.png"
 *   3. Returns the blob URL (for optional preview) or null on failure.
 *
 * `isAvailable` is false when the video element is absent, has not decoded
 * enough data (readyState < HAVE_CURRENT_DATA = 2), or no FootageAnchor is set.
 * The parent should use this to disable the snapshot button.
 *
 * Note: isAvailable is re-evaluated on every render.  Because `anchor` is React
 * state in the parent and changes when playback starts, the button will become
 * enabled automatically.  readyState is not reactive on its own but will be
 * correct at the moment of the render triggered by anchor changing.
 */
export function useSnapshot(
  videoRef: React.RefObject<HTMLVideoElement>,
  anchor: FootageAnchor | null,
  tzOffsetMinutes: number,
  camName: string,
): {
  takeSnapshot: () => Promise<string | null>;
  isAvailable: boolean;
} {
  // Compute isAvailable inline — no extra state; anchor in parent state
  // ensures the component re-renders when the player is ready.
  const video = videoRef.current;
  const isAvailable = !!(video && video.readyState >= 2 && anchor);

  async function takeSnapshot(): Promise<string | null> {
    const vid = videoRef.current;
    if (!vid || vid.readyState < 2 || !anchor) return null;

    const canvas = document.createElement("canvas");
    canvas.width = vid.videoWidth;
    canvas.height = vid.videoHeight;
    const ctx = canvas.getContext("2d");
    if (!ctx) return null;
    ctx.drawImage(vid, 0, 0);

    const footageTs = footageEpoch(anchor, vid.currentTime);
    const filename = buildSnapshotFilename(footageTs, tzOffsetMinutes, camName);

    return new Promise<string | null>((resolve) => {
      canvas.toBlob((blob) => {
        if (!blob) {
          resolve(null);
          return;
        }
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = filename;
        a.click();
        // Revoke after a short delay so the browser has time to start the download.
        setTimeout(() => URL.revokeObjectURL(url), 5000);
        resolve(url);
      }, "image/png");
    });
  }

  return { takeSnapshot, isAvailable };
}
