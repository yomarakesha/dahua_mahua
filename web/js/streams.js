/**
 * MediaMTX API integration, WebRTC/HLS connection lifecycle, stall detection,
 * and auto-disable handling for repeatedly failing NVRs.
 */

import { CONFIG, STALL_CHECK_INTERVAL, STALL_THRESHOLD } from "./config.js";
import { state } from "./state.js";
import { dlog } from "./logger.js";
import {
  getNvrId, scheduleStatusUpdate, showWarning, showToast,
  getPageCameras,
} from "./utils.js";
import { listCameras, listNvrs } from "./api.js";

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
// Sub-stream for crowded grids (saves bandwidth + decoder CPU); main-stream for
// 1×1/2×2 layouts where sub looks blurry. The MediaMTX path for main is the
// sub path + "_main" suffix (backend creates both paths per camera).

export function streamPathFor(camPath) {
  const tiles = (state.gridCols || 1) * (state.gridRows || 1);
  const maxMainTiles = state.prefs && state.prefs.mainStreamMaxTiles
    ? state.prefs.mainStreamMaxTiles : 4;
  return tiles <= maxMainTiles ? camPath + "_main" : camPath;
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
      iceServers: [{ urls: "stun:stun.l.google.com:19302" }],
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

  pc.oniceconnectionstatechange = () => {
    const active = getActiveConnection(path, generation);
    if (!active || active.pc !== pc) return;
    const s = pc.iceConnectionState;
    dlog.debug(path, "ice-state", s);
    if (s === "failed" || s === "disconnected" || s === "closed") {
      dlog.warn(path, "ice-error", s);
      active.lastError = "webrtc-ice-" + s;
      active.status = "error";
      updateCellDot(path, "error");
      scheduleStatusUpdate();
      scheduleReconnect(path, active.video || videoEl, generation);
    } else if (s === "connected" || s === "completed") {
      dlog.info(path, "ice-connected");
      active._lastCheckTime = undefined;
      active._lastCheckTs = undefined;
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

  checkAutoDisable(path);

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

// ── Auto-disable on repeated auth failure ───────────────────────────────────

async function autoDisableNvr(nvrId, reason) {
  if (state.autoDisabledNvrs.has(nvrId)) return;
  state.autoDisabledNvrs.add(nvrId);
  try {
    const { updateNvr } = await import("./api.js");
    await updateNvr(nvrId, { enabled: false });
    showToast(`NVR "${nvrId}" auto-disabled: ${reason}`, "warning", 10000);
    await fetchInventory();
    if (state._onInventoryChanged) state._onInventoryChanged();
  } catch (e) {
    // Auto-disable is best-effort: operator may lack admin rights, or the
    // backend may be unreachable. Don't escalate — the connection will keep
    // backing off on its own.
    dlog.warn(nvrId, "auto-disable-failed", String(e));
  }
}

function checkAutoDisable(path) {
  const conn = state.connections[path];
  if (!conn || conn.failures < 3) return;
  if (conn.lastError && (
    conn.lastError.includes("negotiation-failed") ||
    conn.lastError.includes("ice-failed")
  )) {
    const nvrId = getNvrId(path);
    const nvrMeta = state.inventory && state.inventory.nvrs
      ? state.inventory.nvrs.find(n => n.id === nvrId) : null;
    if (nvrMeta && nvrMeta.enabled !== false) {
      autoDisableNvr(nvrId, `Stream ${path} failed ${conn.failures} times (${conn.lastError})`);
    }
  }
}

export function reconnectAllVisible() {
  const visible = new Set(getPageCameras().filter(Boolean));
  for (const path in state.connections) {
    const c = state.connections[path];
    c.failures = 0;
    if (c.retryTimer) { clearTimeout(c.retryTimer); c.retryTimer = null; }
    if (visible.has(path) && c.video) connectCamera(path, c.video);
  }
  if (state._onGridDirty) state._onGridDirty();
}
