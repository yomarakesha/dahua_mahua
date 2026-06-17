/**
 * WebRTC delivery instrumentation — answers "why did this tile freeze?".
 *
 * The freeze almost never has one obvious cause; the browser's own inbound-rtp
 * stats tell you which layer failed:
 *   • codec = video/H265 + framesDecoded stuck near 0      → browser can't decode
 *     this codec (H.265-over-WebRTC is hardware/OS dependent). Fix on the camera.
 *   • packetsLost / lossPct rising, nackCount climbing      → lossy delivery leg
 *     (flapping link, Wi-Fi, congestion). Fix the network / add FEC.
 *   • freezeCount / totalFreezesDuration rising while loss  → keyframe-recovery
 *     is low and codec decodes fine                          stalls (long GOP).
 *   • jitter high, jitterBufferDelay growing                → path jitter.
 *
 * Samples every live connection periodically (dlog.info) and grabs an immediate
 * snapshot at a detected stall (dlog.warn). `window.__dssRtcStats()` dumps a
 * live snapshot of every connection for ad-hoc inspection / automated tests.
 */

import { state } from "./state.js";
import { dlog } from "./logger.js";

const SAMPLE_MS = 8000;
let _timer = null;

// Pull the inbound video report (+ its negotiated codec) out of a getStats map.
export async function inboundVideoStats(pc) {
  if (!pc || typeof pc.getStats !== "function") return null;
  let report;
  try {
    report = await pc.getStats();
  } catch (_) {
    return null;
  }
  let inbound = null;
  const codecs = {};
  report.forEach((s) => {
    if (s.type === "codec") codecs[s.id] = s;
    if (s.type === "inbound-rtp" && (s.kind === "video" || s.mediaType === "video")) inbound = s;
  });
  if (!inbound) return null;
  const recv = inbound.packetsReceived || 0;
  const lost = inbound.packetsLost || 0;
  const denom = recv + lost;
  return {
    codec: (inbound.codecId && codecs[inbound.codecId] && codecs[inbound.codecId].mimeType) || "?",
    packetsReceived: recv,
    packetsLost: lost,
    lossPct: denom > 0 ? +((100 * lost) / denom).toFixed(2) : 0,
    framesDecoded: inbound.framesDecoded || 0,
    framesDropped: inbound.framesDropped || 0,
    freezeCount: inbound.freezeCount || 0,
    freezeSecs: +(inbound.totalFreezesDuration || 0).toFixed(2),
    jitterMs: +((inbound.jitter || 0) * 1000).toFixed(1),
    nack: inbound.nackCount || 0,
    pli: inbound.pliCount || 0,
    fps: inbound.framesPerSecond || 0,
    res: `${inbound.frameWidth || 0}x${inbound.frameHeight || 0}`,
  };
}

// One-line summary for logs.
function fmt(s) {
  return `codec=${s.codec} fps=${s.fps} res=${s.res} decoded=${s.framesDecoded} dropped=${s.framesDropped} ` +
         `loss=${s.lossPct}%(${s.packetsLost}) freezes=${s.freezeCount}/${s.freezeSecs}s ` +
         `jitter=${s.jitterMs}ms nack=${s.nack} pli=${s.pli}`;
}

// Immediate capture when a stall fires — the most diagnostic moment.
export function captureStall(path, pc) {
  inboundVideoStats(pc).then((s) => {
    if (s) dlog.warn(path, "rtc-stats-at-stall", fmt(s));
  });
}

// Iterate live grid connections + the fullscreen main connection.
function liveConns() {
  const out = [];
  for (const path in state.connections) {
    const c = state.connections[path];
    if (c && c.pc && c.status === "live" && !c.preconnected) out.push([path, c.pc]);
  }
  if (state.fullscreenConn && state.fullscreenConn.pc && state.fullscreenPath) {
    out.push([`${state.fullscreenPath}#main`, state.fullscreenConn.pc]);
  }
  return out;
}

async function sample() {
  for (const [path, pc] of liveConns()) {
    const s = await inboundVideoStats(pc);
    if (!s) continue;
    // Flag the two failure signatures loudly; everything else is info baseline.
    const noDecode = s.packetsReceived > 50 && s.framesDecoded < 2;       // codec can't decode
    const heavyLoss = s.lossPct >= 2 || s.freezeCount > 0;                // delivery problem
    const log = noDecode ? dlog.warn : (heavyLoss ? dlog.warn : dlog.info);
    log(path, noDecode ? "rtc-no-decode" : (heavyLoss ? "rtc-degraded" : "rtc-stats"), fmt(s));
  }
}

export function startStatsSampler() {
  if (_timer) return;
  _timer = setInterval(() => { sample().catch(() => {}); }, SAMPLE_MS);
  // Ad-hoc / test hook: `await window.__dssRtcStats()` → array of {path, ...stats}.
  if (typeof window !== "undefined") {
    window.__dssRtcStats = async () => {
      const rows = [];
      for (const [path, pc] of liveConns()) {
        const s = await inboundVideoStats(pc);
        if (s) rows.push({ path, ...s });
      }
      return rows;
    };
  }
}
