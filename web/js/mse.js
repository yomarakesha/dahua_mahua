/**
 * go2rtc MSE delivery — the buffered alternative to WebRTC/WHEP.
 *
 * WebRTC freezes at 0% packet loss when cameras deliver frames in bursts (a
 * jittery LAN). go2rtc serves a buffered MSE pipeline that absorbs the bursts:
 * a burst is buffered and played out smoothly instead of starving the decoder.
 * Measured on a 6-cam grid: dozens of WebRTC freezes → ~zero MSE stalls.
 *
 * We reuse go2rtc's VideoRTC web component (vendored, MIT) forced to MSE-only,
 * mounted per grid cell. The component owns its WebSocket + MediaSource +
 * reconnect lifecycle; removing the element from the DOM tears it down.
 */

import { VideoRTC } from "./vendor/video-rtc.js";
import { CONFIG } from "./config.js";
import { state } from "./state.js";
import { dlog } from "./logger.js";

if (!customElements.get("dss-mse")) {
  customElements.define("dss-mse", class extends VideoRTC {
    oninit() {
      super.oninit();                 // builds + appends this.video
      if (this.video) this.video.controls = false;   // no native controls in tiles
    }
  });
}

export function isMse() {
  return CONFIG.relay === "go2rtc-mse";
}

function wsFor(streamPath) {
  return new URL(`${CONFIG.go2rtcWsBase}/api/ws?src=${encodeURIComponent(streamPath)}`);
}

/** Mount a buffered-MSE player for `streamPath` into `cell`. Returns the element. */
export function mountMse(cell, streamPath) {
  const el = document.createElement("dss-mse");
  el.mode = "mse";                 // force MSE only (no WebRTC fallback)
  el.background = true;            // keep playing when off-screen
  el.className = "mse-player";
  el.dataset.streamPath = streamPath;
  cell.appendChild(el);
  el.src = wsFor(streamPath);      // setting src kicks off the connection
  return el;
}

/** Point an existing player at a different stream (tier switch / source toggle). */
export function setMseSource(el, streamPath) {
  if (!el) return;
  el.dataset.streamPath = streamPath;
  el.src = wsFor(streamPath);
}

// ── instrumentation (MSE has no getStats; use the media element directly) ────
// Mirrors rtcstats.js so freezes are comparable across relays. A "stall" =
// currentTime not advancing between samples while the element is playing.

const SAMPLE_MS = 8000;
let _timer = null;
const _last = new WeakMap();   // video el -> last currentTime

function players() {
  return [...document.querySelectorAll("dss-mse")].filter((e) => e.isConnected);
}

function sampleOne(el) {
  const v = el.video;
  if (!v) return;
  let q = {};
  try { q = v.getVideoPlaybackQuality ? v.getVideoPlaybackQuality() : {}; } catch (_) {}
  const ct = v.currentTime || 0;
  const prev = _last.get(v);
  _last.set(v, ct);
  const stalled = prev !== undefined && !v.paused && v.readyState >= 2 && ct === prev;
  const path = el.dataset.streamPath || "";
  const msg = `mode=mse rs=${v.readyState} ct=${ct.toFixed(1)} ` +
              `total=${q.totalVideoFrames || 0} dropped=${q.droppedVideoFrames || 0} ` +
              `buffered=${v.buffered && v.buffered.length ? (v.buffered.end(v.buffered.length - 1) - ct).toFixed(2) : 0}s`;
  (stalled ? dlog.warn : dlog.info)(path, stalled ? "mse-stall" : "mse-stats", msg);
}

export function startMseSampler() {
  if (_timer) return;
  _timer = setInterval(() => players().forEach(sampleOne), SAMPLE_MS);
  if (typeof window !== "undefined") {
    window.__dssMseStats = () => players().map((el) => {
      const v = el.video || {};
      let q = {}; try { q = v.getVideoPlaybackQuality ? v.getVideoPlaybackQuality() : {}; } catch (_) {}
      return {
        path: el.dataset.streamPath, rs: v.readyState ?? -1,
        ct: +(v.currentTime || 0).toFixed(2), paused: v.paused ?? true,
        total: q.totalVideoFrames || 0, dropped: q.droppedVideoFrames || 0,
      };
    });
  }
}
