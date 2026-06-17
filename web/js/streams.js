/**
 * MediaMTX API integration, WebRTC/HLS connection lifecycle, stall detection,
 * and auto-disable handling for repeatedly failing NVRs.
 */

import { CONFIG, STALL_CHECK_INTERVAL, STALL_THRESHOLD, ICE_DISCONNECT_GRACE } from "./config.js";
import { state } from "./state.js";
import { dlog } from "./logger.js";
import { decideIceAction } from "./ice.js";
import {
  scheduleStatusUpdate, showWarning,
} from "./utils.js";
import { listCameras, listNvrs } from "./api.js";
import { captureStall, startStatsSampler } from "./rtcstats.js";

// ── MediaMTX API ────────────────────────────────────────────────────────────

export async function fetchCameras() {
  try {
    const cams = await listCameras();
    // Path name is what MediaMTX speaks; build it the same way the backend
    // does (mirrors Camera.mediamtx_path / path_sync.path_name).
    const paths = cams.map(c => `${c.nvr_id}_ch${c.channel}`).sort();
    // Side-table: path → camera_id, so streamPathFor() and the stream URL
    // lookup can resolve a player slot back to the DB id.
    state.cameraByPath = {};
    cams.forEach(c => {
      const p = `${c.nvr_id}_ch${c.channel}`;
      state.cameraByPath[p] = c;
    });
    if (JSON.stringify(paths) !== JSON.stringify(state.allCameras)) {
      dlog.info("", "camera-list-updated", `count=${paths.length} (was ${state.allCameras.length})`);
      state.allCameras = paths;
      if (state._onCamerasChanged) state._onCamerasChanged();
    }
    showWarning(null);
  } catch (e) {
    dlog.error("", "backend-api-error", String(e));
    showWarning("Cannot reach backend — camera list may be stale");
  }
}

export async function fetchInventory() {
  try {
    const nvrs = await listNvrs();
    // Legacy shape the rest of the UI expects: { nvrs: [...] }.
    state.inventory = { nvrs };
  } catch (e) {
    dlog.error("", "inventory-fetch-failed", String(e));
  }
}

// ── Stream tier selection ───────────────────────────────────────────────────
// The grid ALWAYS uses the light sub-stream, regardless of grid size — it keeps
// decoder/network load low when many tiles are on screen. The heavy main-stream
// is pulled only when a single camera is opened fullscreen (see fullscreen.js,
// which requests camPath + "_main" directly). The "_main" path is created by
// the backend alongside the sub path for every camera.

export function streamPathFor(camPath) {
  return camPath;
}

// ── Connection queue ────────────────────────────────────────────────────────
// Cap concurrent WebRTC negotiations to avoid browser/network flooding.

const connQueue = {
  pending: [],
  active: 0,
};

export function queueConnection(path, videoEl) {
  const gen = (state.connections[path] && state.connections[path].generation) || 0;
  connQueue.pending.push({ path, videoEl, generation: gen });
  drainQueue();
}

function drainQueue() {
  while (connQueue.active < state.prefs.maxConcurrent && connQueue.pending.length > 0) {
    const job = connQueue.pending.shift();
    const conn = state.connections[job.path];
    if (!conn || conn.generation !== job.generation) continue;
    connQueue.active++;
    doConnect(job.path, job.videoEl).finally(() => {
      connQueue.active--;
      drainQueue();
    });
  }
}

export function flushQueue() {
  connQueue.pending.length = 0;
  clearTimeout(state._preconnectTimer);
}

// ── Connection helpers ──────────────────────────────────────────────────────

export function getActiveConnection(path, generation) {
  const conn = state.connections[path];
  if (!conn) return null;
  if (generation !== undefined && conn.generation !== generation) return null;
  return conn;
}

function resetVideoElement(videoEl) {
  if (!videoEl) return;
  try { videoEl.pause(); } catch (_) {}
  videoEl.onerror = null;
  videoEl.onplaying = null;
  videoEl.srcObject = null;
  videoEl.removeAttribute("src");
  videoEl.load();
}
export { resetVideoElement };

function bindHlsVideo(path, videoEl, conn) {
  const generation = conn.generation;

  videoEl.onerror = () => {
    const active = getActiveConnection(path, generation);
    if (!active || active.mode !== "hls" || active.video !== videoEl) return;
    active.lastError = "hls-playback-failed";
    active.status = "error";
    updateCellDot(path, "error");
    scheduleStatusUpdate();
    scheduleReconnect(path, videoEl, generation);
  };

  videoEl.onplaying = () => {
    const active = getActiveConnection(path, generation);
    if (!active || active.mode !== "hls" || active.video !== videoEl) return;
    active.status = "live";
    active.failures = 0;
    active.lastError = null;
    updateCellDot(path, "live");
    scheduleStatusUpdate();
  };
}

export function attachConnectionVideo(path, videoEl) {
  const conn = state.connections[path];
  if (!conn) return false;

  const oldVideo = conn.video;
  if (oldVideo !== videoEl) {
    videoEl.autoplay = true;
    videoEl.muted = true;
    videoEl.playsInline = true;

    if (conn.mode === "hls" && conn.hlsUrl) {
      bindHlsVideo(path, videoEl, conn);
      videoEl.srcObject = null;
      if (videoEl.src !== conn.hlsUrl) {
        videoEl.src = conn.hlsUrl;
        videoEl.load();
      }
      videoEl.play().catch(() => {});
    } else if (conn.stream) {
      videoEl.onerror = null;
      videoEl.onplaying = null;
      videoEl.srcObject = conn.stream;
      videoEl.play().catch(() => {});
    } else {
      videoEl.onerror = null;
      videoEl.onplaying = null;
    }

    conn.video = videoEl;
    conn.preconnected = false;

    if (oldVideo && oldVideo !== videoEl) {
      oldVideo.onerror = null;
      oldVideo.onplaying = null;
      if (oldVideo.parentNode === document.body) {
        resetVideoElement(oldVideo);
        oldVideo.remove();
      }
    }
  }

  updateCellDot(path, conn.status);
  scheduleStatusUpdate();
  return true;
}

// ── Main entry: start a fresh connection (cancels any in-flight) ────────────

export function connectCamera(path, videoEl) {
  const existing = state.connections[path];
  if (existing) {
    if (existing.retryTimer) clearTimeout(existing.retryTimer);
    if (existing._iceGraceTimer) clearTimeout(existing._iceGraceTimer);
    if (existing.pc) { try { existing.pc.close(); } catch(_){} }
  }

  const generation = (existing ? existing.generation || 0 : 0) + 1;
  const streamPath = streamPathFor(path);
  resetVideoElement(videoEl);
  state.connections[path] = {
    pc: null,
    status: "connecting",
    video: videoEl,
    failures: existing ? existing.failures || 0 : 0,
    retryTimer: null,
    generation,
    mode: "webrtc",
    stream: null,
    hlsUrl: "",
    preconnected: false,
    lastError: null,
    streamPath,
  };
  dlog.info(path, "stream-tier", `using ${streamPath} (tiles=${state.gridCols}x${state.gridRows})`);
  updateCellDot(path, "connecting");
  scheduleStatusUpdate();
  queueConnection(path, videoEl);
}

async function doConnect(path, videoEl) {
  const conn = state.connections[path];
  if (!conn) return;
  const generation = conn.generation;
  const targetVideo = conn.video || videoEl;

  dlog.info(path, "connect-start", `gen=${generation} failures=${conn.failures}`);

  const webrtcOk = await tryWebRTC(path, targetVideo, conn, generation);
  const active = getActiveConnection(path, generation);
  if (!active) { dlog.debug(path, "connect-aborted", "generation mismatch"); return; }
  if (!webrtcOk) {
    dlog.warn(path, "webrtc-failed, trying HLS");
    const hlsOk = tryHLS(path, active.video || targetVideo, active);
    if (!hlsOk) {
      dlog.error(path, "hls-also-failed");
      active.status = "error";
      updateCellDot(path, "error");
      scheduleStatusUpdate();
      scheduleReconnect(path, active.video || targetVideo, generation);
    }
  }
}

async function tryWebRTC(path, videoEl, conn, generation) {
  let pc;
  try {
    pc = new RTCPeerConnection({
      iceServers: CONFIG.iceServers || [],
    });
  } catch (e) {
    dlog.error(path, "RTCPeerConnection create failed", String(e));
    return false;
  }
  conn.pc = pc;
  conn.mode = "webrtc";
  conn.stream = null;
  conn.hlsUrl = "";

  // VIDEO ONLY — no audio transceiver = significant bandwidth savings
  pc.addTransceiver("video", { direction: "recvonly" });

  pc.ontrack = (evt) => {
    const active = getActiveConnection(path, generation);
    if (!active || active.pc !== pc) return;
    dlog.info(path, "webrtc-track-received", `streams=${evt.streams.length}`);
    active.stream = evt.streams[0];
    if (active.video) {
      active.video.srcObject = active.stream;
      active._lastCheckTime = undefined;
      active._lastCheckTs = undefined;
      const playPromise = active.video.play();
      if (playPromise) playPromise.catch(() => {});
    }
    active.status = "live";
    active.failures = 0;
    active.lastError = null;
    updateCellDot(path, "live");
    scheduleStatusUpdate();
  };

  const reconnectFromIce = (active, s) => {
    if (active._iceGraceTimer) { clearTimeout(active._iceGraceTimer); active._iceGraceTimer = null; }
    dlog.warn(path, "ice-error", s);
    active.lastError = "webrtc-ice-" + s;
    active.status = "error";
    updateCellDot(path, "error");
    scheduleStatusUpdate();
    scheduleReconnect(path, active.video || videoEl, generation);
  };

  pc.oniceconnectionstatechange = () => {
    const active = getActiveConnection(path, generation);
    if (!active || active.pc !== pc) return;
    const s = pc.iceConnectionState;
    dlog.debug(path, "ice-state", s);
    switch (decideIceAction(s, !!active._iceGraceTimer)) {
      case "reconnect":
        reconnectFromIce(active, s);
        break;
      case "start-grace":
        // `disconnected` often self-heals — wait before tearing down. If it
        // recovers, oniceconnectionstatechange fires again with `connected`
        // and cancels this timer; otherwise we reconnect when it expires.
        dlog.info(path, "ice-disconnected-grace", `waiting ${ICE_DISCONNECT_GRACE}ms`);
        active._iceGraceTimer = setTimeout(() => {
          const cur = getActiveConnection(path, generation);
          if (!cur || cur.pc !== pc) return;
          cur._iceGraceTimer = null;
          const st = pc.iceConnectionState;
          if (st === "connected" || st === "completed") return; // recovered
          reconnectFromIce(cur, st);
        }, ICE_DISCONNECT_GRACE);
        break;
      case "cancel-grace":
        if (active._iceGraceTimer) { clearTimeout(active._iceGraceTimer); active._iceGraceTimer = null; }
        dlog.info(path, "ice-connected");
        active._lastCheckTime = undefined;
        active._lastCheckTs = undefined;
        break;
      // "ignore": intermediate states (checking/new) — nothing to do.
    }
  };

  try {
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    const wpath = conn.streamPath || path;
    dlog.debug(path, "whep-request", `${CONFIG.webrtcBase}/${wpath}/whep`);
    const abort = new AbortController();
    const timer = setTimeout(() => abort.abort(), 10000);
    const res = await fetch(`${CONFIG.webrtcBase}/${wpath}/whep`, {
      method: "POST",
      headers: { "Content-Type": "application/sdp" },
      body: pc.localDescription.sdp,
      signal: abort.signal,
    });
    clearTimeout(timer);
    if (!res.ok) {
      const body = await res.text().catch(() => "");
      dlog.error(path, "whep-http-error", `status=${res.status} body=${body.slice(0, 200)}`);
      throw new Error(`WHEP ${res.status}`);
    }
    const answer = await res.text();
    dlog.debug(path, "whep-answer-received", `len=${answer.length}`);
    await pc.setRemoteDescription({ type: "answer", sdp: answer });
    dlog.info(path, "webrtc-negotiation-ok");
    return true;
  } catch (e) {
    const msg = e.name === "AbortError" ? "whep-timeout-10s" : String(e).slice(0, 150);
    dlog.error(path, "webrtc-negotiation-failed", msg);
    const active = getActiveConnection(path, generation);
    if (active && active.pc === pc) {
      active.pc = null;
      active.lastError = "webrtc-negotiation-failed";
    }
    try { pc.close(); } catch(_e){}
    return false;
  }
}

function tryHLS(path, videoEl, conn) {
  const hpath = conn.streamPath || path;
  const hlsUrl = `${CONFIG.hlsBase}/${hpath}/index.m3u8`;
  dlog.info(path, "hls-fallback", hlsUrl);
  conn.pc = null;
  conn.mode = "hls";
  conn.stream = null;
  conn.hlsUrl = hlsUrl;

  bindHlsVideo(path, videoEl, conn);
  videoEl.srcObject = null;
  videoEl.src = hlsUrl;
  videoEl.load();
  videoEl.play().catch(() => {});
  return true;
}

function scheduleReconnect(path, videoEl, generation) {
  const conn = getActiveConnection(path, generation);
  if (!conn) return;

  if (conn.retryTimer) {
    clearTimeout(conn.retryTimer);
    conn.retryTimer = null;
  }

  conn.failures = (conn.failures || 0) + 1;

  const maxRetries = state.prefs.maxRetries;
  if (maxRetries >= 0 && conn.failures > maxRetries) {
    dlog.warn(path, "max-retries-reached", `failures=${conn.failures} max=${maxRetries} lastErr=${conn.lastError}`);
    conn.status = "error";
    updateCellDot(path, "error");
    scheduleStatusUpdate();
    return;
  }

  const baseDelay = Math.max(1000, state.prefs.retryDelay * 1000);
  const backoff = Math.min(CONFIG.reconnectMax, baseDelay * (2 ** Math.max(0, conn.failures - 1)));
  const jitter = Math.floor(backoff * (Math.random() * 0.3 - 0.15));
  const delay = Math.max(baseDelay, backoff + jitter);
  dlog.info(path, "schedule-reconnect", `failures=${conn.failures} delay=${Math.round(delay/1000)}s lastErr=${conn.lastError}`);

  conn.retryTimer = setTimeout(() => {
    const active = getActiveConnection(path, generation);
    if (active) {
      active.retryTimer = null;
      connectCamera(path, active.video || videoEl);
    }
  }, delay);
}

export function disconnectCamera(path) {
  const entry = state.connections[path];
  if (entry) {
    if (entry.retryTimer) clearTimeout(entry.retryTimer);
    if (entry._iceGraceTimer) clearTimeout(entry._iceGraceTimer);
    if (entry.pc) { try { entry.pc.close(); } catch(_){} }
    if (entry.video) {
      resetVideoElement(entry.video);
      if (entry.preconnected && entry.video.parentNode === document.body) {
        entry.video.remove();
      }
    }
    delete state.connections[path];
  }
}

export function disconnectAllNotVisible(visibleSet, preconnectSet) {
  for (const p in state.connections) {
    if (!visibleSet.has(p) && !(preconnectSet && preconnectSet.has(p))) disconnectCamera(p);
  }
}

export function updateCellDot(path, status) {
  const dot = document.querySelector('[data-dot-path="' + path + '"]');
  if (dot) dot.className = "status-dot " + status;
}

// ── Stall detection ─────────────────────────────────────────────────────────

export function startStallDetection() {
  if (state.stallCheckTimer) return;
  state.stallCheckTimer = setInterval(checkForStalls, STALL_CHECK_INTERVAL);
  startStatsSampler();   // periodic WebRTC inbound-rtp telemetry → server logs
}

function checkForStalls() {
  const now = Date.now();
  for (const path in state.connections) {
    const conn = state.connections[path];
    if (conn.status !== "live" || !conn.video) continue;

    const video = conn.video;
    if (conn.preconnected) continue;

    const curTime = video.currentTime;
    const readyState = video.readyState;

    if (conn._lastCheckTime === undefined) {
      conn._lastCheckTime = curTime;
      conn._lastCheckTs = now;
      continue;
    }

    const elapsed = (now - conn._lastCheckTs) / 1000;

    if (video.paused && video.readyState >= 2) {
      video.play().catch(() => {});
    }

    if (elapsed >= STALL_THRESHOLD && curTime === conn._lastCheckTime && readyState < 3) {
      dlog.warn(path, "video-stall-detected", `readyState=${readyState} curTime=${curTime} elapsed=${elapsed.toFixed(1)}s mode=${conn.mode}`);
      if (conn.pc) captureStall(path, conn.pc);   // grab WebRTC stats at the freeze
      conn.status = "error";
      conn.lastError = "video-stall";
      updateCellDot(path, "error");
      scheduleStatusUpdate();
      scheduleReconnect(path, video, conn.generation);
      continue;
    }

    if (curTime !== conn._lastCheckTime) {
      conn._lastCheckTime = curTime;
      conn._lastCheckTs = now;
    }
  }
}

// ── Manual reconnect ────────────────────────────────────────────────────────
// NOTE: the browser deliberately does NOT auto-disable NVRs. A WebRTC/ICE/HLS
// failure here is a *transport* problem (MediaMTX restart, network blip), not a
// credential problem — disabling the NVR from the client would wipe it from the
// inventory on a transient hiccup and require a manual re-enable. The server-side
// source watchdog owns disable decisions: it polls MediaMTX's real source state
// and skips entirely when MediaMTX is unreachable, so a restart never trips it.

export function reconnectAllVisible() {
  // Reconnect every visible grid cell, regardless of its current connection
  // state — even cells whose connection entry was dropped or is stuck. We read
  // the live <video> straight from the DOM so a missing state.connections entry
  // can't make the button a no-op (the old bug).
  const cells = document.querySelectorAll("#camera-grid .cam-cell[data-path]");
  let n = 0;
  cells.forEach(cell => {
    const path = cell.dataset.path;
    const video = cell.querySelector("video");
    if (!path || !video) return;
    const conn = state.connections[path];
    if (conn) {
      conn.failures = 0;
      if (conn.retryTimer) { clearTimeout(conn.retryTimer); conn.retryTimer = null; }
    }
    connectCamera(path, video);
    n++;
  });
  dlog.info("", "reconnect-all", `reconnecting ${n} visible cell(s)`);
  if (state._onGridDirty) state._onGridDirty();
}
