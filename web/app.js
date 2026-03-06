/**
 * DSS Camera Dashboard — Optimized surveillance UI
 *
 * Performance-critical design:
 *  - Connection queue: max N concurrent WebRTC negotiations
 *  - Video-only (no audio transceiver) — halves bandwidth
 *  - Batched DOM updates via requestAnimationFrame
 *  - Exponential backoff on reconnect
 *  - Disconnect-before-connect on page changes
 */

(function () {
"use strict";

// ===== CONFIGURATION ==========================================================

const CONFIG = {
  apiBase: `${location.protocol}//${location.hostname}:9997/v3`,
  webrtcBase: `${location.protocol}//${location.hostname}:8889`,
  hlsBase: `${location.protocol}//${location.hostname}:8888`,
  pollInterval: 10000,
  maxConcurrent: 8,        // max simultaneous WebRTC negotiations
  reconnectBase: 2000,     // base reconnect delay (ms)
  reconnectMax: 30000,     // max reconnect delay (ms)
  gridPresets: [2, 4, 8, 16, 32, 64],
  patrolIntervals: [5, 10, 15, 30, 60],
};

// ===== LOCAL STORAGE KEYS =====================================================

const LS = {
  groups:   "dss_groups",
  layouts:  "dss_layouts",
  prefs:    "dss_prefs",
};

// ===== STATE ==================================================================

const state = {
  allCameras: [],
  filteredCameras: [],
  gridCols: 4,
  gridRows: 4,
  currentPage: 0,
  searchText: "",
  activeFilter: { type: "all", value: "" },
  groups: [],
  layouts: [],
  prefs: { gridCols: 4, gridRows: 4, patrolInterval: 10, sidebarOpen: true, lastLayout: "",
           maxRetries: 3, retryDelay: 10, maxConcurrent: 8 },
  customOrder: null,
  connections: {},       // path -> { pc, status, video, failures, retryTimer, generation }
  patrol: { active: false, timer: null, countdown: 0, paused: false },
  focusedCell: -1,
  fullscreenPath: null,
  fullscreenConn: null,  // { pc } for main-stream in fullscreen
  inventory: null,        // fetched NVR inventory (labels, metadata)
};

// ===== CONNECTION QUEUE =======================================================
// Only allow CONFIG.maxConcurrent WebRTC negotiations at once.
// Prevents browser/network flooding when loading 64+ cameras.

const connQueue = {
  pending: [],    // [{ path, videoEl, generation }]
  active: 0,      // currently negotiating count
};

function queueConnection(path, videoEl) {
  // Tag with generation so stale queue items are skipped
  const gen = (state.connections[path] && state.connections[path].generation) || 0;
  connQueue.pending.push({ path, videoEl, generation: gen });
  drainQueue();
}

function drainQueue() {
  while (connQueue.active < state.prefs.maxConcurrent && connQueue.pending.length > 0) {
    const job = connQueue.pending.shift();
    const conn = state.connections[job.path];
    // Skip stale jobs (camera was disconnected or reconnected with new generation)
    if (!conn || conn.generation !== job.generation) continue;
    connQueue.active++;
    doConnect(job.path, job.videoEl).finally(() => {
      connQueue.active--;
      drainQueue();
    });
  }
}

function flushQueue() {
  connQueue.pending.length = 0;
  clearTimeout(state._preconnectTimer);
}

// ===== DOM REFS ===============================================================

const $ = (id) => document.getElementById(id);
const dom = {
  sidebarToggle:  $("sidebar-toggle"),
  sidebar:        $("sidebar"),
  gridSizeSel:    $("grid-size"),
  layoutSel:      $("layout-select"),
  saveLayoutBtn:  $("save-layout-btn"),
  patrolBtn:      $("patrol-btn"),
  patrolInterval: $("patrol-interval"),
  patrolCountdown:$("patrol-countdown"),
  search:         $("search"),
  statusToggle:   $("status-toggle"),
  statusSummary:  $("status-summary"),
  shortcutsBtn:   $("shortcuts-btn"),
  showAllBtn:     $("show-all-btn"),
  addGroupBtn:    $("add-group-btn"),
  nvrTree:        $("nvr-tree"),
  groupTree:      $("group-tree"),
  gridContainer:  $("grid-container"),
  cameraGrid:     $("camera-grid"),
  sbOnline:       $("sb-online"),
  sbConnecting:   $("sb-connecting"),
  sbError:        $("sb-error"),
  sbTotal:        $("sb-total"),
  sbPage:         $("sb-page"),
  sbFilter:       $("sb-filter"),
  fsOverlay:      $("fullscreen-overlay"),
  fsTitle:        $("fs-title"),
  fsVideo:        $("fs-video"),
  fsBuffer:       $("fs-buffer"),
  fsSnapshotBtn:  $("fs-snapshot-btn"),
  fsCloseBtn:     $("fs-close-btn"),
  statusPanel:    $("status-panel"),
  statusList:     $("status-list"),
  reconnectAllBtn:$("reconnect-all-btn"),
  shortcutsModal: $("shortcuts-modal"),
  groupDialog:    $("group-dialog"),
  groupNameInput: $("group-name-input"),
  groupCreateBtn: $("group-create-btn"),
  groupCancelBtn: $("group-cancel-btn"),
  layoutDialog:   $("layout-dialog"),
  layoutNameInput:$("layout-name-input"),
  layoutSaveBtn:  $("layout-save-btn"),
  layoutCancelBtn:$("layout-cancel-btn"),
  contextMenu:    $("context-menu"),
  contextItems:   $("context-menu-items"),
  settingsBtn:    $("settings-btn"),
  settingsModal:  $("settings-modal"),
  settingsPort:   $("settings-port"),
  settingsUser:   $("settings-user"),
  settingsPass:   $("settings-pass"),
  settingsSubtype:$("settings-subtype"),
  settingsNvrCount:$("settings-nvr-count"),
  settingsNvrBody:$("settings-nvr-body"),
  settingsNewId:  $("settings-new-id"),
  settingsNewLabel:$("settings-new-label"),
  settingsNewIp:  $("settings-new-ip"),
  settingsNewCh:  $("settings-new-ch"),
  settingsNewPass:$("settings-new-pass"),
  settingsAddBtn: $("settings-add-btn"),
  settingsSaveBtn:$("settings-save-btn"),
  settingsRestartBtn:$("settings-restart-btn"),
  settingsStatus: $("settings-status"),
  settingsMaxRetries: $("settings-max-retries"),
  settingsRetryDelay: $("settings-retry-delay"),
  settingsMaxConcurrent: $("settings-max-concurrent"),
  settingsCurPw:  $("settings-cur-pw"),
  settingsNewPw:  $("settings-new-pw"),
  settingsChpwBtn:$("settings-chpw-btn"),
  settingsChpwStatus:$("settings-chpw-status"),
  logoutBtn:      $("logout-btn"),
  warningBanner:  $("warning-banner"),
};

// ===== PERSISTENCE ============================================================

function loadState() {
  try {
    const g = localStorage.getItem(LS.groups);
    if (g) state.groups = JSON.parse(g);
    const l = localStorage.getItem(LS.layouts);
    if (l) state.layouts = JSON.parse(l);
    const p = localStorage.getItem(LS.prefs);
    if (p) Object.assign(state.prefs, JSON.parse(p));
    // Migrate old gridSize pref to gridCols/gridRows
    if (state.prefs.gridSize && !state.prefs.gridCols) {
      state.prefs.gridCols = state.prefs.gridSize;
      state.prefs.gridRows = state.prefs.gridSize;
      delete state.prefs.gridSize;
    }
    state.gridCols = state.prefs.gridCols || 4;
    state.gridRows = state.prefs.gridRows || 4;
  } catch (_) {}
}

function saveGroups()  { localStorage.setItem(LS.groups, JSON.stringify(state.groups)); }
function saveLayouts() { localStorage.setItem(LS.layouts, JSON.stringify(state.layouts)); }
function savePrefs()   { localStorage.setItem(LS.prefs, JSON.stringify(state.prefs)); }

function gridCells() { return state.gridCols * state.gridRows; }

// ===== HELPERS ================================================================

function getNvrId(path)   { return path.split("_")[0]; }
function getChannel(path) { return path.split("_").slice(1).join("_"); }
function formatName(path) { return path.replace(/_/g, " / ").toUpperCase(); }

function getNvrList() {
  const nvrs = new Map();
  state.allCameras.forEach(p => {
    const id = getNvrId(p);
    if (!nvrs.has(id)) nvrs.set(id, []);
    nvrs.get(id).push(p);
  });
  // Include NVRs from inventory even if MediaMTX hasn't listed their paths yet
  if (state.inventory && state.inventory.nvrs) {
    state.inventory.nvrs.forEach(nvr => {
      if (!nvrs.has(nvr.id)) nvrs.set(nvr.id, []);
    });
  }
  return nvrs;
}

function totalPages() {
  return Math.max(1, Math.ceil(state.filteredCameras.length / gridCells()));
}

function getPageCameras() {
  const perPage = gridCells();
  const start = state.currentPage * perPage;
  return state.filteredCameras.slice(start, start + perPage);
}

function getNextPageCameras() {
  const tp = totalPages();
  if (tp <= 1) return [];
  const nextPage = (state.currentPage + 1) % tp;
  const perPage = gridCells();
  const start = nextPage * perPage;
  return state.filteredCameras.slice(start, start + perPage);
}

// ===== BATCHED STATUS UPDATES =================================================
// Coalesce rapid status changes into single DOM update per frame.

let statusDirty = false;

function scheduleStatusUpdate() {
  if (statusDirty) return;
  statusDirty = true;
  requestAnimationFrame(() => {
    statusDirty = false;
    doUpdateStatusBar();
    doUpdateSidebarDots();
  });
}

function doUpdateStatusBar() {
  let online = 0, connecting = 0, errored = 0, total = 0;
  for (const path in state.connections) {
    const c = state.connections[path];
    total++;
    if (c.status === "live") online++;
    else if (c.status === "connecting") connecting++;
    else if (c.status === "error") errored++;
  }
  dom.sbOnline.textContent = "Online: " + online;
  dom.sbConnecting.textContent = "Connecting: " + connecting;
  dom.sbError.textContent = "Error: " + errored;
  dom.statusSummary.textContent = online + "/" + total;
  const dotEl = dom.statusToggle.querySelector(".status-dot");
  dotEl.className = "status-dot " + (errored > 0 ? "error" : connecting > 0 ? "connecting" : "online");
}

function doUpdateSidebarDots() {
  const dots = document.querySelectorAll(".tree-cam-dot");
  for (let i = 0; i < dots.length; i++) {
    const el = dots[i].parentElement;
    if (!el) continue;
    const path = el.dataset.path;
    if (!path) continue;
    const conn = state.connections[path];
    dots[i].className = "tree-cam-dot" + (conn ? " " + conn.status : "");
  }
}

// ===== MEDIAMTX API ===========================================================

async function fetchCameras() {
  try {
    // Fetch all pages from MediaMTX API (default page size is limited)
    let allItems = [];
    let page = 0;
    while (true) {
      const res = await fetch(`${CONFIG.apiBase}/paths/list?itemsPerPage=500&page=${page}`);
      if (!res.ok) throw new Error(`API ${res.status}`);
      const data = await res.json();
      const items = data.items || data;
      if (!Array.isArray(items) || items.length === 0) break;
      allItems = allItems.concat(items);
      // If we got fewer than requested or no pageCount info, we're done
      if (!data.pageCount || page + 1 >= data.pageCount) break;
      page++;
    }
    const paths = allItems.map(i => i.name).filter(n => !n.endsWith("_main")).sort();
    if (JSON.stringify(paths) !== JSON.stringify(state.allCameras)) {
      state.allCameras = paths;
      applyFilter();
      renderSidebar();
    }
    showWarning(null);
  } catch (e) {
    showWarning("Cannot reach MediaMTX — camera list may be stale");
  }
}

async function fetchInventory() {
  try {
    const res = await fetch("/api/inventory");
    if (res.status === 401) { location.href = "/login"; return; }
    if (res.ok) state.inventory = await res.json();
  } catch (_) {}
}

function showWarning(msg) {
  if (msg) {
    dom.warningBanner.textContent = msg;
    dom.warningBanner.classList.remove("hidden");
  } else {
    dom.warningBanner.classList.add("hidden");
  }
}

// ===== CAMERA CONNECTIONS =====================================================

// Prepare a connection slot (track in state) and enqueue negotiation.
function connectCamera(path, videoEl) {
  // Cancel any pending retry
  const existing = state.connections[path];
  if (existing) {
    if (existing.retryTimer) clearTimeout(existing.retryTimer);
    if (existing.pc) { try { existing.pc.close(); } catch(_){} }
  }

  const generation = (existing ? existing.generation || 0 : 0) + 1;
  state.connections[path] = {
    pc: null,
    status: "connecting",
    video: videoEl,
    failures: existing ? existing.failures || 0 : 0,
    retryTimer: null,
    generation,
    mode: "webrtc", // "webrtc" or "hls"
  };
  updateCellDot(path, "connecting");
  scheduleStatusUpdate();
  queueConnection(path, videoEl);
}

// Actual WebRTC negotiation — called from queue, returns promise.
async function doConnect(path, videoEl) {
  const conn = state.connections[path];
  if (!conn) return;

  // Try WebRTC first, fall back to HLS on failure
  const webrtcOk = await tryWebRTC(path, videoEl, conn);
  if (!webrtcOk) {
    // WebRTC failed — try HLS immediately (don't waste a retry)
    const hlsOk = tryHLS(path, videoEl, conn);
    if (!hlsOk) {
      conn.status = "error";
      updateCellDot(path, "error");
      scheduleStatusUpdate();
      scheduleReconnect(path, videoEl);
    }
  }
}

async function tryWebRTC(path, videoEl, conn) {
  let pc;
  try {
    pc = new RTCPeerConnection({
      iceServers: [{ urls: "stun:stun.l.google.com:19302" }],
    });
  } catch (e) {
    return false;
  }
  conn.pc = pc;
  conn.mode = "webrtc";

  // VIDEO ONLY — no audio transceiver = significant bandwidth savings
  pc.addTransceiver("video", { direction: "recvonly" });

  pc.ontrack = (evt) => {
    if (state.connections[path] && state.connections[path].pc === pc) {
      videoEl.srcObject = evt.streams[0];
      conn.status = "live";
      conn.failures = 0;
      updateCellDot(path, "live");
      scheduleStatusUpdate();
    }
  };

  pc.oniceconnectionstatechange = () => {
    if (state.connections[path] && state.connections[path].pc !== pc) return;
    const s = pc.iceConnectionState;
    if (s === "failed" || s === "disconnected") {
      if (state.connections[path]) {
        state.connections[path].status = "error";
      }
      updateCellDot(path, "error");
      scheduleStatusUpdate();
      scheduleReconnect(path, videoEl);
    }
  };

  try {
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    const abort = new AbortController();
    const timer = setTimeout(() => abort.abort(), 10000);
    const res = await fetch(`${CONFIG.webrtcBase}/${path}/whep`, {
      method: "POST",
      headers: { "Content-Type": "application/sdp" },
      body: pc.localDescription.sdp,
      signal: abort.signal,
    });
    clearTimeout(timer);
    if (!res.ok) throw new Error(`WHEP ${res.status}`);
    const answer = await res.text();
    await pc.setRemoteDescription({ type: "answer", sdp: answer });
    return true;
  } catch (_) {
    try { pc.close(); } catch(_e){}
    return false;
  }
}

function tryHLS(path, videoEl, conn) {
  const hlsUrl = `${CONFIG.hlsBase}/${path}/index.m3u8`;
  conn.pc = null;
  conn.mode = "hls";

  // Native HLS (Safari) or MSE-based via <video> src
  if (videoEl.canPlayType("application/vnd.apple.mpegurl")) {
    videoEl.src = hlsUrl;
  } else {
    // Most browsers support HLS via MediaSource Extensions with fMP4
    // MediaMTX serves fMP4 HLS which Chrome/Firefox can play natively
    videoEl.src = hlsUrl;
  }

  videoEl.onerror = () => {
    if (state.connections[path] && state.connections[path].mode === "hls") {
      conn.status = "error";
      updateCellDot(path, "error");
      scheduleStatusUpdate();
      scheduleReconnect(path, videoEl);
    }
  };

  videoEl.onplaying = () => {
    if (state.connections[path] && state.connections[path].mode === "hls") {
      conn.status = "live";
      conn.failures = 0;
      updateCellDot(path, "live");
      scheduleStatusUpdate();
    }
  };

  videoEl.load();
  videoEl.play().catch(() => {});
  return true;
}

// Reconnect with configurable max retries and delay
function scheduleReconnect(path, videoEl) {
  const conn = state.connections[path];
  if (!conn) return;

  conn.failures = (conn.failures || 0) + 1;
  const maxRetries = state.prefs.maxRetries;

  // Stop retrying if max retries reached (0 = no retry, -1 = infinite)
  if (maxRetries >= 0 && conn.failures > maxRetries) {
    conn.status = "error";
    updateCellDot(path, "error");
    scheduleStatusUpdate();
    return;
  }

  const delay = state.prefs.retryDelay * 1000;

  conn.retryTimer = setTimeout(() => {
    if (state.connections[path]) {
      connectCamera(path, videoEl);
    }
  }, delay);
}

function disconnectCamera(path) {
  const entry = state.connections[path];
  if (entry) {
    if (entry.retryTimer) clearTimeout(entry.retryTimer);
    if (entry.pc) { try { entry.pc.close(); } catch(_){} }
    if (entry.video) {
      entry.video.srcObject = null;
      entry.video.removeAttribute("src");
      entry.video.onerror = null;
      entry.video.onplaying = null;
      // Remove hidden preconnect video elements from DOM
      if (entry.preconnected && entry.video.parentNode === document.body) {
        entry.video.remove();
      }
    }
    delete state.connections[path];
  }
}

function disconnectAllNotVisible(visibleSet, preconnectSet) {
  for (const p in state.connections) {
    if (!visibleSet.has(p) && !(preconnectSet && preconnectSet.has(p))) disconnectCamera(p);
  }
}

// ===== FILTERING ==============================================================

function applyFilter(type, value) {
  if (type !== undefined) {
    state.activeFilter = { type, value };
    state.currentPage = 0;
    state.customOrder = null;
  }

  let cams = state.allCameras.slice();
  const f = state.activeFilter;

  if (f.type === "nvr") {
    cams = cams.filter(p => getNvrId(p) === f.value);
  } else if (f.type === "group") {
    const grp = state.groups.find(g => g.name === f.value);
    if (grp) {
      const set = new Set(grp.cameras);
      cams = cams.filter(p => set.has(p));
    }
  }

  if (state.searchText) {
    const q = state.searchText.toLowerCase();
    cams = cams.filter(p => p.toLowerCase().includes(q) || formatName(p).toLowerCase().includes(q));
  }

  if (state.customOrder) {
    const ordered = [];
    const remaining = new Set(cams);
    state.customOrder.forEach(p => {
      if (remaining.has(p)) { ordered.push(p); remaining.delete(p); }
    });
    remaining.forEach(p => ordered.push(p));
    cams = ordered;
  }

  state.filteredCameras = cams;

  // Auto-fit grid when filtering to a specific NVR or group
  if (type === "nvr" || type === "group") {
    autoFitGrid(cams.length);
  } else {
    const tp = totalPages();
    if (state.currentPage >= tp) state.currentPage = tp - 1;
    if (state.currentPage < 0) state.currentPage = 0;
    renderGrid();
  }

  updateSidebarActive();
  updateFilterLabel();
  scheduleStatusUpdate();
}

function updateFilterLabel() {
  const f = state.activeFilter;
  if (f.type === "nvr") dom.sbFilter.textContent = f.value.toUpperCase();
  else if (f.type === "group") dom.sbFilter.textContent = "Group: " + f.value;
  else dom.sbFilter.textContent = "All cameras";
}

// ===== SIDEBAR ================================================================

function renderSidebar() {
  renderNvrTree();
  renderGroupTree();
}

function renderNvrTree() {
  const nvrs = getNvrList();
  dom.nvrTree.innerHTML = "";

  [...nvrs.keys()].sort().forEach(nvrId => {
    const cameras = nvrs.get(nvrId);
    const node = document.createElement("div");

    const header = document.createElement("div");
    header.className = "tree-nvr";
    header.dataset.nvrId = nvrId;

    const arrow = document.createElement("span");
    arrow.className = "tree-arrow";
    arrow.textContent = "\u25B6";

    // Use inventory label if available
    const nvrMeta = state.inventory && state.inventory.nvrs
      ? state.inventory.nvrs.find(n => n.id === nvrId) : null;
    const displayLabel = nvrMeta && nvrMeta.label ? nvrMeta.label : nvrId.toUpperCase();

    const label = document.createElement("span");
    label.textContent = `${displayLabel} (${cameras.length})`;
    label.style.cssText = "overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1";

    header.appendChild(arrow);
    header.appendChild(label);

    const children = document.createElement("div");
    children.className = "tree-children";

    cameras.forEach(cam => {
      const camEl = document.createElement("div");
      camEl.className = "tree-camera";
      camEl.draggable = true;
      camEl.dataset.path = cam;

      const dot = document.createElement("span");
      dot.className = "tree-cam-dot";
      const conn = state.connections[cam];
      if (conn) dot.classList.add(conn.status);

      camEl.appendChild(dot);
      camEl.appendChild(document.createTextNode(getChannel(cam).toUpperCase()));

      camEl.addEventListener("click", () => jumpToCamera(cam));
      camEl.addEventListener("dragstart", (e) => {
        e.dataTransfer.setData("text/plain", cam);
        e.dataTransfer.effectAllowed = "move";
      });

      children.appendChild(camEl);
    });

    header.addEventListener("click", (e) => {
      if (e.detail === 1) {
        arrow.classList.toggle("open");
        children.classList.toggle("open");
      }
    });

    header.addEventListener("dblclick", () => applyFilter("nvr", nvrId));

    header.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      showContextMenu(e, [
        { label: "Show NVR", action: () => applyFilter("nvr", nvrId) },
        { label: "Rename", action: () => renameNvr(nvrId) },
      ]);
    });

    node.appendChild(header);
    node.appendChild(children);
    dom.nvrTree.appendChild(node);
  });
}

function renderGroupTree() {
  dom.groupTree.innerHTML = "";

  state.groups.forEach(grp => {
    const node = document.createElement("div");
    const header = document.createElement("div");
    header.className = "tree-group";
    header.dataset.groupName = grp.name;

    const arrow = document.createElement("span");
    arrow.className = "tree-arrow";
    arrow.textContent = "\u25B6";

    const label = document.createElement("span");
    label.textContent = `${grp.name} (${grp.cameras.length})`;

    header.appendChild(arrow);
    header.appendChild(label);

    const children = document.createElement("div");
    children.className = "tree-children";

    grp.cameras.forEach(cam => {
      const camEl = document.createElement("div");
      camEl.className = "tree-camera";
      camEl.draggable = true;
      camEl.dataset.path = cam;

      const dot = document.createElement("span");
      dot.className = "tree-cam-dot";
      const conn = state.connections[cam];
      if (conn) dot.classList.add(conn.status);

      camEl.appendChild(dot);
      camEl.appendChild(document.createTextNode(formatName(cam)));

      camEl.addEventListener("click", () => jumpToCamera(cam));
      camEl.addEventListener("dragstart", (e) => {
        e.dataTransfer.setData("text/plain", cam);
        e.dataTransfer.effectAllowed = "move";
      });

      camEl.addEventListener("contextmenu", (e) => {
        e.preventDefault();
        showContextMenu(e, [
          { label: "Remove from group", action: () => removeFromGroup(grp.name, cam) },
        ]);
      });

      children.appendChild(camEl);
    });

    header.addEventListener("click", (e) => {
      if (e.detail === 1) {
        arrow.classList.toggle("open");
        children.classList.toggle("open");
      }
    });

    header.addEventListener("dblclick", () => applyFilter("group", grp.name));

    header.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      showContextMenu(e, [
        { label: "Show group", action: () => applyFilter("group", grp.name) },
        { label: "Delete group", action: () => deleteGroup(grp.name) },
      ]);
    });

    header.addEventListener("dragover", (e) => { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; header.style.background = "#252540"; });
    header.addEventListener("dragleave", () => { header.style.background = ""; });
    header.addEventListener("drop", (e) => {
      e.preventDefault();
      header.style.background = "";
      const cam = e.dataTransfer.getData("text/plain");
      if (cam && state.allCameras.includes(cam)) addToGroup(grp.name, cam);
    });

    node.appendChild(header);
    node.appendChild(children);
    dom.groupTree.appendChild(node);
  });
}

function updateSidebarActive() {
  dom.nvrTree.querySelectorAll(".tree-nvr").forEach(el => {
    el.classList.toggle("active", state.activeFilter.type === "nvr" && state.activeFilter.value === el.dataset.nvrId);
  });
  dom.groupTree.querySelectorAll(".tree-group").forEach(el => {
    el.classList.toggle("active", state.activeFilter.type === "group" && state.activeFilter.value === el.dataset.groupName);
  });
}

function jumpToCamera(cam) {
  const idx = state.filteredCameras.indexOf(cam);
  if (idx === -1) {
    applyFilter("all", "");
    const idx2 = state.filteredCameras.indexOf(cam);
    if (idx2 === -1) return;
    goToPage(Math.floor(idx2 / gridCells()));
    return;
  }
  const page = Math.floor(idx / gridCells());
  if (page !== state.currentPage) goToPage(page);
  setFocusedCell(idx % gridCells());
}

// ===== GRID RENDERING =========================================================

function renderGrid() {
  const pageCams = getPageCameras();
  const cols = state.gridCols;
  const rows = state.gridRows;
  const visibleSet = new Set(pageCams);
  const nextCams = getNextPageCameras();
  const preconnectSet = new Set(nextCams);

  // Flush pending queue — don't start connections for cameras we're about to leave
  flushQueue();

  // Disconnect cameras not on this page or next page
  disconnectAllNotVisible(visibleSet, preconnectSet);

  // Set grid CSS
  dom.cameraGrid.style.gridTemplateColumns = `repeat(${cols}, 1fr)`;
  dom.cameraGrid.style.gridTemplateRows = `repeat(${rows}, 1fr)`;
  // Use max dimension for CSS class (controls label/dot visibility on large grids)
  dom.cameraGrid.className = `grid-${Math.max(cols, rows)}`;

  // Build DOM in fragment to avoid reflows
  const frag = document.createDocumentFragment();
  const totalSlots = cols * rows;

  for (let i = 0; i < totalSlots; i++) {
    const path = pageCams[i] || null;
    const cell = document.createElement("div");
    cell.className = "cam-cell";
    cell.dataset.index = i;

    if (path) {
      cell.dataset.path = path;

      const video = document.createElement("video");
      video.autoplay = true;
      video.muted = true;
      video.playsInline = true;

      const dot = document.createElement("div");
      dot.className = "status-dot";
      dot.dataset.dotPath = path;

      const label = document.createElement("div");
      label.className = "label";
      label.textContent = formatName(path);

      const controls = document.createElement("div");
      controls.className = "cell-controls";
      const snapBtn = document.createElement("button");
      snapBtn.className = "cell-btn";
      snapBtn.innerHTML = "&#128247;";
      snapBtn.title = "Snapshot";
      snapBtn.addEventListener("click", (e) => { e.stopPropagation(); takeSnapshot(path, video); });
      controls.appendChild(snapBtn);

      cell.appendChild(video);
      cell.appendChild(dot);
      cell.appendChild(label);
      cell.appendChild(controls);

      cell.addEventListener("click", () => setFocusedCell(i));
      cell.addEventListener("dblclick", () => openFullscreen(path));

      cell.addEventListener("contextmenu", (e) => {
        e.preventDefault();
        const items = [
          { label: "Fullscreen", action: () => openFullscreen(path) },
          { label: "Snapshot", action: () => takeSnapshot(path, video) },
          { label: "Reconnect", action: () => connectCamera(path, video) },
          { type: "separator" },
        ];
        state.groups.forEach(grp => {
          const inGroup = grp.cameras.includes(path);
          items.push({
            label: (inGroup ? "\u2212 " : "+ ") + grp.name,
            action: () => inGroup ? removeFromGroup(grp.name, path) : addToGroup(grp.name, path),
          });
        });
        showContextMenu(e, items);
      });

      cell.draggable = true;
      setupDragDrop(cell, path, i);


      // Reuse preconnected stream if available, otherwise queue new connection
      const preConn = state.connections[path];
      if (preConn && preConn.preconnected && (preConn.status === "live" || preConn.status === "connecting")) {
        const oldVideo = preConn.video;
        // Transfer the stream from the hidden preconnect video
        if (preConn.mode === "hls" && oldVideo && oldVideo.src) {
          video.src = oldVideo.src;
          video.load();
          video.play().catch(() => {});
        } else if (oldVideo && oldVideo.srcObject) {
          video.srcObject = oldVideo.srcObject;
        }
        // Clean up hidden video element
        if (oldVideo && oldVideo.parentNode === document.body) {
          oldVideo.srcObject = null;
          oldVideo.removeAttribute("src");
          oldVideo.onerror = null;
          oldVideo.onplaying = null;
          oldVideo.remove();
        }
        // Update the connection to point to the visible video element
        preConn.video = video;
        preConn.preconnected = false;
        updateCellDot(path, preConn.status);
      } else {
        connectCamera(path, video);
      }
    } else {
      cell.style.background = "#0a0a0a";
      cell.addEventListener("dragover", (e) => { e.preventDefault(); cell.classList.add("drag-over"); });
      cell.addEventListener("dragleave", () => cell.classList.remove("drag-over"));
      cell.addEventListener("drop", (e) => {
        e.preventDefault();
        cell.classList.remove("drag-over");
        const cam = e.dataTransfer.getData("text/plain");
        if (cam) handleDrop(cam, i);
      });
    }

    frag.appendChild(cell);
  }

  dom.cameraGrid.innerHTML = "";
  dom.cameraGrid.appendChild(frag);

  const tp = totalPages();
  dom.sbPage.textContent = tp > 1 ? `Page ${state.currentPage + 1}/${tp}` : "";
  dom.sbTotal.textContent = `Total: ${state.filteredCameras.length}`;
  state.focusedCell = -1;

  // Preconnect next page streams after a short delay (let current page finish first)
  if (tp > 1) {
    clearTimeout(state._preconnectTimer);
    state._preconnectTimer = setTimeout(preconnectNextPage, 2000);
  }
}

function preconnectNextPage() {
  const nextCams = getNextPageCameras();
  if (nextCams.length === 0) return;

  nextCams.forEach(path => {
    if (!path) return;
    // Skip if already connected (visible or preconnected)
    if (state.connections[path]) return;

    // Create a hidden video element for the preconnection
    const video = document.createElement("video");
    video.autoplay = true;
    video.muted = true;
    video.playsInline = true;
    video.style.display = "none";
    document.body.appendChild(video);

    // Mark connection as preconnected so renderGrid can identify and reuse it
    connectCamera(path, video);
    const conn = state.connections[path];
    if (conn) conn.preconnected = true;
  });
}

function cleanupPreconnected() {
  // Remove hidden video elements from preconnected streams that are no longer needed
  for (const p in state.connections) {
    const conn = state.connections[p];
    if (conn.preconnected && conn.video && conn.video.parentNode === document.body) {
      conn.video.srcObject = null;
      conn.video.removeAttribute("src");
      conn.video.remove();
    }
  }
}

function updateCellDot(path, status) {
  const dot = document.querySelector('[data-dot-path="' + path + '"]');
  if (dot) dot.className = "status-dot " + status;
}

function setFocusedCell(index) {
  dom.cameraGrid.querySelectorAll(".cam-cell.focused").forEach(el => el.classList.remove("focused"));
  state.focusedCell = index;
  const cells = dom.cameraGrid.querySelectorAll(".cam-cell");
  if (cells[index]) cells[index].classList.add("focused");
}

function getFocusedPath() {
  if (state.focusedCell < 0) return null;
  return getPageCameras()[state.focusedCell] || null;
}

// ===== PAGE NAVIGATION ========================================================

function goToPage(page) {
  const tp = totalPages();
  if (page < 0) page = tp - 1;
  if (page >= tp) page = 0;
  if (page === state.currentPage) return;
  state.currentPage = page;
  renderGrid();
}

function nextPage() { goToPage(state.currentPage + 1); }
function prevPage() { goToPage(state.currentPage - 1); }

// ===== GROUPS =================================================================

function createGroup(name) {
  if (!name || state.groups.find(g => g.name === name)) return;
  state.groups.push({ name, cameras: [] });
  saveGroups();
  renderGroupTree();
}

function addToGroup(groupName, cameraPath) {
  const grp = state.groups.find(g => g.name === groupName);
  if (!grp || grp.cameras.includes(cameraPath)) return;
  grp.cameras.push(cameraPath);
  saveGroups();
  renderGroupTree();
}

function removeFromGroup(groupName, cameraPath) {
  const grp = state.groups.find(g => g.name === groupName);
  if (!grp) return;
  grp.cameras = grp.cameras.filter(c => c !== cameraPath);
  saveGroups();
  renderGroupTree();
  if (state.activeFilter.type === "group" && state.activeFilter.value === groupName) applyFilter();
}

function deleteGroup(name) {
  state.groups = state.groups.filter(g => g.name !== name);
  saveGroups();
  renderGroupTree();
  if (state.activeFilter.type === "group" && state.activeFilter.value === name) applyFilter("all", "");
}

function showGroupDialog() {
  dom.groupDialog.classList.remove("hidden");
  dom.groupNameInput.value = "";
  dom.groupNameInput.focus();
}

function hideGroupDialog() { dom.groupDialog.classList.add("hidden"); }

async function renameNvr(nvrId) {
  if (!state.inventory) return;
  const nvr = state.inventory.nvrs.find(n => n.id === nvrId);
  const current = nvr ? (nvr.label || nvrId) : nvrId;
  const newName = prompt("Rename NVR:", current);
  if (!newName || newName === current) return;
  if (nvr) nvr.label = newName;
  try {
    const res = await fetch("/api/inventory", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(state.inventory),
    });
    if (res.ok) renderSidebar();
  } catch (_) {}
}

// ===== LAYOUTS ================================================================

function saveLayout(name) {
  if (!name) return;
  state.layouts = state.layouts.filter(l => l.name !== name);
  state.layouts.push({
    name,
    gridCols: state.gridCols,
    gridRows: state.gridRows,
    filter: { ...state.activeFilter },
    cameraOrder: state.customOrder ? [...state.customOrder] : null,
    page: state.currentPage,
  });
  saveLayouts();
  renderLayoutSelect();
  state.prefs.lastLayout = name;
  savePrefs();
}

function loadLayout(name) {
  const layout = state.layouts.find(l => l.name === name);
  if (!layout) return;
  state.gridCols = layout.gridCols || layout.gridSize || 4;
  state.gridRows = layout.gridRows || layout.gridSize || 4;
  state.customOrder = layout.cameraOrder ? [...layout.cameraOrder] : null;
  updateGridSizeInput();
  state.prefs.gridCols = state.gridCols;
  state.prefs.gridRows = state.gridRows;
  state.prefs.lastLayout = name;
  savePrefs();
  applyFilter(layout.filter.type, layout.filter.value);
  if (layout.page !== undefined) goToPage(layout.page);
}

function deleteLayout(name) {
  state.layouts = state.layouts.filter(l => l.name !== name);
  saveLayouts();
  renderLayoutSelect();
}

function renderLayoutSelect() {
  dom.layoutSel.innerHTML = '<option value="">— Select —</option>';
  state.layouts.forEach(l => {
    const opt = document.createElement("option");
    opt.value = l.name;
    opt.textContent = l.name;
    dom.layoutSel.appendChild(opt);
  });
  if (state.layouts.length > 0) {
    const sep = document.createElement("option");
    sep.disabled = true;
    sep.textContent = "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500";
    dom.layoutSel.appendChild(sep);
    state.layouts.forEach(l => {
      const opt = document.createElement("option");
      opt.value = "__delete__" + l.name;
      opt.textContent = "\u2715 Delete: " + l.name;
      dom.layoutSel.appendChild(opt);
    });
  }
}

function showLayoutDialog() {
  dom.layoutDialog.classList.remove("hidden");
  dom.layoutNameInput.value = "";
  dom.layoutNameInput.focus();
}

function hideLayoutDialog() { dom.layoutDialog.classList.add("hidden"); }

// ===== DRAG & DROP ============================================================

function setupDragDrop(cell, path, index) {
  cell.addEventListener("dragstart", (e) => {
    e.dataTransfer.setData("text/plain", path);
    e.dataTransfer.effectAllowed = "move";
    cell.classList.add("dragging");
  });
  cell.addEventListener("dragend", () => cell.classList.remove("dragging"));
  cell.addEventListener("dragover", (e) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    cell.classList.add("drag-over");
  });
  cell.addEventListener("dragleave", () => cell.classList.remove("drag-over"));
  cell.addEventListener("drop", (e) => {
    e.preventDefault();
    cell.classList.remove("drag-over");
    const cam = e.dataTransfer.getData("text/plain");
    if (cam) handleDrop(cam, index);
  });
}

function handleDrop(draggedPath, targetIndex) {
  if (!draggedPath) return;
  const perPage = gridCells();
  const pageOffset = state.currentPage * perPage;

  if (!state.customOrder) state.customOrder = [...state.filteredCameras];
  const order = state.customOrder;
  const dragIdx = order.indexOf(draggedPath);
  const targetGlobalIdx = pageOffset + targetIndex;

  if (dragIdx === -1) {
    order.splice(targetGlobalIdx, 0, draggedPath);
  } else if (dragIdx !== targetGlobalIdx) {
    order.splice(dragIdx, 1);
    order.splice(Math.min(targetGlobalIdx, order.length), 0, draggedPath);
  }
  applyFilter();
}

// ===== PATROL MODE ============================================================

function startPatrol() {
  if (state.patrol.active) return;
  state.patrol.active = true;
  state.patrol.paused = false;
  const interval = parseInt(dom.patrolInterval.value) || 10;
  state.patrol.countdown = interval;

  dom.patrolBtn.classList.add("active");
  dom.patrolBtn.innerHTML = "&#9724; Stop";

  state.patrol.timer = setInterval(() => {
    if (state.patrol.paused) return;
    state.patrol.countdown--;
    dom.patrolCountdown.textContent = state.patrol.countdown + "s";
    if (state.patrol.countdown <= 0) {
      nextPage();
      state.patrol.countdown = interval;
    }
  }, 1000);

  dom.patrolCountdown.textContent = interval + "s";
}

function stopPatrol() {
  state.patrol.active = false;
  state.patrol.paused = false;
  if (state.patrol.timer) { clearInterval(state.patrol.timer); state.patrol.timer = null; }
  dom.patrolBtn.classList.remove("active");
  dom.patrolBtn.innerHTML = "&#9654; Patrol";
  dom.patrolCountdown.textContent = "";
}

function togglePatrol() {
  if (state.patrol.active) stopPatrol(); else startPatrol();
}

dom.gridContainer.addEventListener("mouseenter", () => { if (state.patrol.active) state.patrol.paused = true; });
dom.gridContainer.addEventListener("mouseleave", () => { if (state.patrol.active) state.patrol.paused = false; });

// ===== DIGITAL ZOOM (removed) ================================================

// ===== KEYBOARD SHORTCUTS =====================================================

function setupKeyboard() {
  document.addEventListener("keydown", (e) => {
    const tag = e.target.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") {
      if (e.key === "Escape") e.target.blur();
      return;
    }

    if (e.key === "Escape") {
      if (state.fullscreenPath) { closeFullscreen(); return; }
      if (!dom.statusPanel.classList.contains("hidden")) { dom.statusPanel.classList.add("hidden"); return; }
      if (!dom.shortcutsModal.classList.contains("hidden")) { dom.shortcutsModal.classList.add("hidden"); return; }
      if (!dom.settingsModal.classList.contains("hidden")) { dom.settingsModal.classList.add("hidden"); return; }
      if (!dom.groupDialog.classList.contains("hidden")) { hideGroupDialog(); return; }
      if (!dom.layoutDialog.classList.contains("hidden")) { hideLayoutDialog(); return; }
      hideContextMenu();
      return;
    }

    if (e.key >= "1" && e.key <= "6" && !e.ctrlKey && !e.metaKey) {
      const s = CONFIG.gridPresets[parseInt(e.key) - 1];
      setGridSize(s, s);
      e.preventDefault();
      return;
    }

    if (e.key === "ArrowLeft")  { prevPage(); e.preventDefault(); return; }
    if (e.key === "ArrowRight") { nextPage(); e.preventDefault(); return; }
    if (e.key === " ") { togglePatrol(); e.preventDefault(); return; }

    if (e.key === "f" || e.key === "F") {
      const p = getFocusedPath();
      if (p) openFullscreen(p);
      e.preventDefault();
      return;
    }

    if (e.key === "s" || e.key === "S") {
      if (state.fullscreenPath) {
        takeSnapshot(state.fullscreenPath, dom.fsVideo);
      } else {
        const p = getFocusedPath();
        if (p && state.connections[p]) takeSnapshot(p, state.connections[p].video);
      }
      e.preventDefault();
      return;
    }

    if (e.key === "/") { dom.search.focus(); e.preventDefault(); return; }
    if (e.key === "?") { toggleModal(dom.shortcutsModal); e.preventDefault(); return; }
    if (e.key === "g" || e.key === "G") { showGroupDialog(); e.preventDefault(); return; }
    if (e.key === ",") { openSettings(); e.preventDefault(); return; }
    if (e.key === "Tab") { toggleSidebar(); e.preventDefault(); return; }
  });
}

// ===== SNAPSHOT ===============================================================

function takeSnapshot(path, videoEl) {
  if (!videoEl || !videoEl.videoWidth) return;
  const canvas = document.createElement("canvas");
  canvas.width = videoEl.videoWidth;
  canvas.height = videoEl.videoHeight;
  canvas.getContext("2d").drawImage(videoEl, 0, 0);
  canvas.toBlob((blob) => {
    const ts = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${path}_${ts}.jpg`;
    a.click();
    URL.revokeObjectURL(a.href);
  }, "image/jpeg", 0.95);
}

// ===== FULLSCREEN =============================================================

function openFullscreen(path) {
  const conn = state.connections[path];
  state.fullscreenPath = path;
  dom.fsTitle.textContent = formatName(path);

  // Show sub-stream immediately — never show black
  if (conn && conn.video && conn.video.srcObject) {
    dom.fsVideo.srcObject = conn.video.srcObject;
  } else if (conn && conn.video && conn.video.src) {
    dom.fsVideo.src = conn.video.src;
  }

  dom.fsOverlay.classList.remove("hidden");
  dom.fsOverlay.requestFullscreen().catch(() => {});

  // Buffer main-stream in hidden video, swap only when frames are ready
  connectFullscreenMain(path);
}

async function connectFullscreenMain(path) {
  disconnectFullscreenMain();
  const mainPath = path + "_main";

  // Try WebRTC main stream first
  let pc;
  try {
    pc = new RTCPeerConnection({
      iceServers: [{ urls: "stun:stun.l.google.com:19302" }],
    });
  } catch (_) {
    tryFullscreenHLS(mainPath);
    return;
  }
  state.fullscreenConn = { pc, swapped: false };

  pc.addTransceiver("video", { direction: "recvonly" });

  pc.ontrack = (evt) => {
    if (!state.fullscreenConn || state.fullscreenConn.pc !== pc) return;
    // Buffer in hidden video first
    dom.fsBuffer.srcObject = evt.streams[0];

    // Wait for actual frames before swapping
    const onReady = () => {
      if (!state.fullscreenConn || state.fullscreenConn.pc !== pc) return;
      if (dom.fsBuffer.videoWidth > 0) {
        // Main stream has frames — swap it in
        dom.fsVideo.srcObject = dom.fsBuffer.srcObject;
        dom.fsBuffer.srcObject = null;
        state.fullscreenConn.swapped = true;
      }
    };
    dom.fsBuffer.addEventListener("playing", onReady, { once: true });
    // Fallback: check after 2s in case 'playing' already fired
    setTimeout(onReady, 2000);
  };

  pc.oniceconnectionstatechange = () => {
    if (!state.fullscreenConn || state.fullscreenConn.pc !== pc) return;
    const s = pc.iceConnectionState;
    if (s === "failed" || s === "disconnected") {
      if (!state.fullscreenConn.swapped) {
        // WebRTC main failed before swap — try HLS main
        try { pc.close(); } catch(_){}
        tryFullscreenHLS(mainPath);
      }
    }
  };

  try {
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    const abort = new AbortController();
    const timer = setTimeout(() => abort.abort(), 10000);
    const res = await fetch(`${CONFIG.webrtcBase}/${mainPath}/whep`, {
      method: "POST",
      headers: { "Content-Type": "application/sdp" },
      body: pc.localDescription.sdp,
      signal: abort.signal,
    });
    clearTimeout(timer);
    if (!res.ok) throw new Error(`WHEP ${res.status}`);
    const answer = await res.text();
    await pc.setRemoteDescription({ type: "answer", sdp: answer });
  } catch (_) {
    // WebRTC negotiation failed — try HLS as fallback
    try { pc.close(); } catch(_e){}
    if (state.fullscreenConn && !state.fullscreenConn.swapped) {
      tryFullscreenHLS(mainPath);
    }
  }
}

function tryFullscreenHLS(mainPath) {
  // HLS fallback for main stream — sub-stream stays visible until this loads
  const hlsUrl = `${CONFIG.hlsBase}/${mainPath}/index.m3u8`;
  dom.fsBuffer.srcObject = null;
  dom.fsBuffer.src = hlsUrl;
  dom.fsBuffer.load();
  dom.fsBuffer.play().catch(() => {});

  const onReady = () => {
    if (!state.fullscreenPath) return;
    if (dom.fsBuffer.videoWidth > 0) {
      dom.fsVideo.srcObject = null;
      dom.fsVideo.src = hlsUrl;
      dom.fsVideo.load();
      dom.fsVideo.play().catch(() => {});
      dom.fsBuffer.removeAttribute("src");
      dom.fsBuffer.load();
    }
  };
  dom.fsBuffer.addEventListener("playing", onReady, { once: true });
  // Keep showing sub-stream if HLS also fails — no black screen ever
}

function disconnectFullscreenMain() {
  if (state.fullscreenConn) {
    try { state.fullscreenConn.pc.close(); } catch (_) {}
    state.fullscreenConn = null;
  }
  dom.fsBuffer.srcObject = null;
  dom.fsBuffer.removeAttribute("src");
}

function closeFullscreen() {
  disconnectFullscreenMain();
  dom.fsOverlay.classList.add("hidden");
  dom.fsVideo.srcObject = null;
  dom.fsVideo.removeAttribute("src");
  state.fullscreenPath = null;
  if (document.fullscreenElement) {
    document.exitFullscreen().catch(() => {});
  }
}

// Handle browser fullscreen exit (e.g. user presses Escape natively)
document.addEventListener("fullscreenchange", () => {
  if (!document.fullscreenElement && state.fullscreenPath) {
    disconnectFullscreenMain();
    dom.fsOverlay.classList.add("hidden");
    dom.fsVideo.srcObject = null;
    dom.fsVideo.removeAttribute("src");
    state.fullscreenPath = null;
  }
});

// ===== STATUS PANEL ===========================================================

function renderStatusPanel() {
  dom.statusList.innerHTML = "";
  const order = { error: 0, connecting: 1, live: 2 };
  const entries = Object.entries(state.connections).sort((a, b) => (order[a[1].status] || 3) - (order[b[1].status] || 3));

  entries.forEach(([path, conn]) => {
    const item = document.createElement("div");
    item.className = "status-item";

    const dot = document.createElement("span");
    dot.className = "status-dot " + conn.status;

    const name = document.createElement("span");
    name.className = "cam-name";
    name.textContent = formatName(path);

    const st = document.createElement("span");
    st.textContent = conn.status;
    st.style.cssText = "color:#666;font-size:11px";

    const btn = document.createElement("button");
    btn.textContent = "Reconnect";
    btn.addEventListener("click", () => { if (conn.video) connectCamera(path, conn.video); });

    item.append(dot, name, st, btn);
    dom.statusList.appendChild(item);
  });
}

// ===== CONTEXT MENU ===========================================================

function showContextMenu(e, items) {
  hideContextMenu();
  dom.contextItems.innerHTML = "";
  items.forEach(item => {
    if (item.type === "separator") {
      const sep = document.createElement("div");
      sep.className = "ctx-separator";
      dom.contextItems.appendChild(sep);
      return;
    }
    const el = document.createElement("div");
    el.className = "ctx-item";
    el.textContent = item.label;
    el.addEventListener("click", () => { hideContextMenu(); item.action(); });
    dom.contextItems.appendChild(el);
  });

  dom.contextMenu.style.left = e.clientX + "px";
  dom.contextMenu.style.top = e.clientY + "px";
  dom.contextMenu.classList.remove("hidden");

  requestAnimationFrame(() => {
    const r = dom.contextMenu.getBoundingClientRect();
    if (r.right > window.innerWidth) dom.contextMenu.style.left = (window.innerWidth - r.width - 4) + "px";
    if (r.bottom > window.innerHeight) dom.contextMenu.style.top = (window.innerHeight - r.height - 4) + "px";
  });
}

function hideContextMenu() { dom.contextMenu.classList.add("hidden"); }
document.addEventListener("click", hideContextMenu);

// ===== MODALS =================================================================

function toggleModal(modal) { modal.classList.toggle("hidden"); }

document.querySelectorAll(".modal").forEach(modal => {
  modal.addEventListener("click", (e) => { if (e.target === modal) modal.classList.add("hidden"); });
  modal.querySelectorAll(".modal-close").forEach(btn => {
    btn.addEventListener("click", () => modal.classList.add("hidden"));
  });
});

// ===== SIDEBAR TOGGLE =========================================================

function toggleSidebar() {
  state.prefs.sidebarOpen = !state.prefs.sidebarOpen;
  dom.sidebar.classList.toggle("collapsed", !state.prefs.sidebarOpen);
  savePrefs();
}

// ===== GRID SIZE ==============================================================

function setGridSize(cols, rows) {
  state.gridCols = cols;
  state.gridRows = rows || cols;
  state.prefs.gridCols = state.gridCols;
  state.prefs.gridRows = state.gridRows;
  updateGridSizeInput();
  state.currentPage = 0;
  savePrefs();
  renderGrid();
}

function updateGridSizeInput() {
  dom.gridSizeSel.value = `${state.gridCols}x${state.gridRows}`;
}

function parseGridInput(val) {
  const m = val.match(/^(\d+)\s*[x×X]\s*(\d+)$/);
  if (m) {
    const c = Math.max(1, Math.min(64, parseInt(m[1])));
    const r = Math.max(1, Math.min(64, parseInt(m[2])));
    return [c, r];
  }
  const n = parseInt(val);
  if (n >= 1 && n <= 64) return [n, n];
  return null;
}

function autoFitGrid(cameraCount) {
  if (cameraCount <= 0) return;
  const s = Math.ceil(Math.sqrt(cameraCount));
  // Use smallest rectangle: s columns, enough rows to fit all
  const rows = Math.ceil(cameraCount / s);
  setGridSize(s, rows);
}

// ===== EVENT BINDINGS =========================================================

function bindEvents() {
  dom.sidebarToggle.addEventListener("click", toggleSidebar);
  dom.showAllBtn.addEventListener("click", () => applyFilter("all", ""));

  // Grid size input: parse on Enter or blur
  dom.gridSizeSel.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      dom.gridSizeSel.blur();
    }
  });
  dom.gridSizeSel.addEventListener("blur", () => {
    const parsed = parseGridInput(dom.gridSizeSel.value);
    if (parsed) {
      setGridSize(parsed[0], parsed[1]);
    } else {
      updateGridSizeInput(); // revert to current
    }
  });

  dom.search.addEventListener("input", () => {
    state.searchText = dom.search.value;
    state.currentPage = 0;
    applyFilter();
  });

  dom.patrolBtn.addEventListener("click", togglePatrol);
  dom.patrolInterval.addEventListener("change", () => {
    state.prefs.patrolInterval = parseInt(dom.patrolInterval.value);
    savePrefs();
    if (state.patrol.active) { stopPatrol(); startPatrol(); }
  });

  dom.layoutSel.addEventListener("change", () => {
    const val = dom.layoutSel.value;
    if (!val) return;
    if (val.startsWith("__delete__")) { deleteLayout(val.replace("__delete__", "")); dom.layoutSel.value = ""; }
    else loadLayout(val);
  });

  dom.saveLayoutBtn.addEventListener("click", showLayoutDialog);
  dom.layoutSaveBtn.addEventListener("click", () => {
    const name = dom.layoutNameInput.value.trim();
    if (name) { saveLayout(name); hideLayoutDialog(); }
  });
  dom.layoutCancelBtn.addEventListener("click", hideLayoutDialog);
  dom.layoutNameInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { const n = dom.layoutNameInput.value.trim(); if (n) { saveLayout(n); hideLayoutDialog(); } }
  });

  dom.addGroupBtn.addEventListener("click", showGroupDialog);
  dom.groupCreateBtn.addEventListener("click", () => {
    const name = dom.groupNameInput.value.trim();
    if (name) { createGroup(name); hideGroupDialog(); }
  });
  dom.groupCancelBtn.addEventListener("click", hideGroupDialog);
  dom.groupNameInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { const n = dom.groupNameInput.value.trim(); if (n) { createGroup(n); hideGroupDialog(); } }
  });

  dom.statusToggle.addEventListener("click", () => { renderStatusPanel(); toggleModal(dom.statusPanel); });
  dom.reconnectAllBtn.addEventListener("click", () => {
    for (const path in state.connections) {
      const c = state.connections[path];
      if (c.status === "error" && c.video) connectCamera(path, c.video);
    }
  });

  dom.shortcutsBtn.addEventListener("click", () => toggleModal(dom.shortcutsModal));
  dom.fsCloseBtn.addEventListener("click", closeFullscreen);
  dom.fsSnapshotBtn.addEventListener("click", () => { if (state.fullscreenPath) takeSnapshot(state.fullscreenPath, dom.fsVideo); });

  // Settings
  dom.settingsBtn.addEventListener("click", openSettings);
  dom.settingsAddBtn.addEventListener("click", addNvrFromFooter);
  dom.settingsSaveBtn.addEventListener("click", saveSettings);
  dom.settingsRestartBtn.addEventListener("click", forceRestart);

  // Auth
  dom.logoutBtn.addEventListener("click", logout);
  dom.settingsChpwBtn.addEventListener("click", changePassword);
}

// ===== NVR SETTINGS ===========================================================

function openSettings() {
  setSettingsStatus("");
  fetch("/api/inventory")
    .then(r => {
      if (r.status === 401) { location.href = "/login"; return; }
      return r.json();
    })
    .then(inv => {
      if (!inv) return;
      renderSettingsForm(inv);
      dom.settingsModal.classList.remove("hidden");
    })
    .catch(e => setSettingsStatus("Failed to load inventory: " + e, true));
}

function renderSettingsForm(inv) {
  const g = inv.global || {};
  dom.settingsPort.value = g.default_port || 554;
  dom.settingsUser.value = g.default_username || "";
  dom.settingsPass.value = g.default_password || "";
  dom.settingsSubtype.value = g.default_subtype != null ? g.default_subtype : 1;

  // Connection settings from prefs
  dom.settingsMaxRetries.value = state.prefs.maxRetries;
  dom.settingsRetryDelay.value = state.prefs.retryDelay;
  dom.settingsMaxConcurrent.value = state.prefs.maxConcurrent;

  dom.settingsNvrBody.innerHTML = "";
  (inv.nvrs || []).forEach(nvr => appendNvrRow(nvr));
  updateNvrCount();
  clearAddRow();
}

function appendNvrRow(nvr) {
  const tr = document.createElement("tr");
  tr.innerHTML =
    `<td><input type="text" value="${esc(nvr.id)}" data-field="id"></td>` +
    `<td><input type="text" value="${esc(nvr.label || "")}" data-field="label"></td>` +
    `<td><input type="text" value="${esc(nvr.ip)}" data-field="ip"></td>` +
    `<td><input type="number" min="1" value="${nvr.channels || 1}" data-field="channels"></td>` +
    `<td><input type="text" value="${esc(nvr.password || "")}" placeholder="(global)" data-field="password"></td>` +
    `<td><button class="settings-row-btn del" title="Remove">&times;</button></td>`;

  // Delete button
  tr.querySelector(".del").addEventListener("click", () => { tr.remove(); updateNvrCount(); });

  dom.settingsNvrBody.appendChild(tr);
}

function esc(s) { return String(s).replace(/"/g, "&quot;").replace(/</g, "&lt;"); }

function updateNvrCount() {
  dom.settingsNvrCount.textContent = dom.settingsNvrBody.querySelectorAll("tr").length;
}

function clearAddRow() {
  dom.settingsNewId.value = nextNvrId();
  dom.settingsNewLabel.value = "";
  dom.settingsNewIp.value = "";
  dom.settingsNewCh.value = 1;
  dom.settingsNewPass.value = "";
}

function nextNvrId() {
  const rows = dom.settingsNvrBody.querySelectorAll("tr");
  let max = 0;
  rows.forEach(r => {
    const id = r.querySelector('[data-field="id"]').value;
    const m = id.match(/nvr(\d+)/);
    if (m) max = Math.max(max, parseInt(m[1]));
  });
  return "nvr" + String(max + 1).padStart(2, "0");
}

function addNvrFromFooter() {
  const id = dom.settingsNewId.value.trim();
  const ip = dom.settingsNewIp.value.trim();
  const ch = parseInt(dom.settingsNewCh.value) || 1;
  if (!id || !ip) { setSettingsStatus("ID and IP are required", true); return; }
  if (!/^(\d{1,3}\.){3}\d{1,3}$/.test(ip)) { setSettingsStatus("Invalid IP format", true); return; }
  if (ch < 1 || ch > 256) { setSettingsStatus("Channels must be 1-256", true); return; }
  appendNvrRow({
    id,
    label: dom.settingsNewLabel.value.trim() || `Dahua NVR ${ip}`,
    ip,
    channels: ch,
    password: dom.settingsNewPass.value,
  });
  updateNvrCount();
  clearAddRow();
  dom.settingsNewIp.focus();
}

function harvestInventory() {
  const inv = {
    global: {
      default_port: parseInt(dom.settingsPort.value) || 554,
      default_username: dom.settingsUser.value.trim() || "admin",
      default_password: dom.settingsPass.value,
      default_subtype: parseInt(dom.settingsSubtype.value),
    },
    nvrs: [],
  };
  dom.settingsNvrBody.querySelectorAll("tr").forEach(tr => {
    const get = f => (tr.querySelector(`[data-field="${f}"]`) || {}).value || "";
    const nvr = {
      id: get("id").trim(),
      label: get("label").trim(),
      ip: get("ip").trim(),
      channels: parseInt(get("channels")) || 1,
      group: "dahua",
    };
    const pw = get("password");
    if (pw) nvr.password = pw;
    inv.nvrs.push(nvr);
  });
  return inv;
}

function setSettingsStatus(msg, isErr) {
  dom.settingsStatus.textContent = msg;
  dom.settingsStatus.className = isErr ? "err" : msg ? "ok" : "";
}

function validateInventory(inv) {
  const ipRe = /^(\d{1,3}\.){3}\d{1,3}$/;
  const port = inv.global.default_port;
  if (port < 1 || port > 65535) return "Port must be 1-65535";
  if (!inv.global.default_username) return "Global username is required";
  const ids = new Set();
  for (const nvr of inv.nvrs) {
    if (!nvr.id) return "NVR ID is required";
    if (ids.has(nvr.id)) return `Duplicate NVR ID: ${nvr.id}`;
    ids.add(nvr.id);
    if (!nvr.ip) return `${nvr.id}: IP address is required`;
    if (!ipRe.test(nvr.ip)) return `${nvr.id}: Invalid IP format (${nvr.ip})`;
    if (nvr.channels < 1 || nvr.channels > 256) return `${nvr.id}: Channels must be 1-256`;
  }
  return null;
}

async function saveSettings() {
  // Save connection prefs
  state.prefs.maxRetries = parseInt(dom.settingsMaxRetries.value);
  state.prefs.retryDelay = Math.max(1, parseInt(dom.settingsRetryDelay.value) || 10);
  state.prefs.maxConcurrent = Math.max(1, Math.min(32, parseInt(dom.settingsMaxConcurrent.value) || 4));
  if (isNaN(state.prefs.maxRetries)) state.prefs.maxRetries = 3;
  savePrefs();

  const inv = harvestInventory();
  const err = validateInventory(inv);
  if (err) { setSettingsStatus(err, true); return; }
  dom.settingsSaveBtn.disabled = true;
  setSettingsStatus("Saving...");
  try {
    const res = await fetch("/api/inventory", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(inv),
    });
    if (res.status === 401) { location.href = "/login"; return; }
    const data = await res.json();
    if (!res.ok) {
      setSettingsStatus(data.error || "Save failed", true);
      return;
    }
    setSettingsStatus(data.message || "Saved & applied");
    // Re-fetch cameras + inventory after config regeneration + MediaMTX restart
    setTimeout(async () => {
      await fetchInventory();
      await fetchCameras();
      reconnectAllVisible();
    }, 1500);
  } catch (e) {
    setSettingsStatus("Network error: " + e, true);
  } finally {
    dom.settingsSaveBtn.disabled = false;
  }
}

async function forceRestart() {
  dom.settingsRestartBtn.disabled = true;
  setSettingsStatus("Restarting MediaMTX...");
  try {
    const res = await fetch("/api/restart", { method: "POST" });
    const data = await res.json();
    if (!res.ok) { setSettingsStatus(data.error || "Restart failed", true); return; }
    setSettingsStatus("MediaMTX restarted");
    setTimeout(() => reconnectAllVisible(), 1500);
  } catch (e) {
    setSettingsStatus("Network error: " + e, true);
  } finally {
    dom.settingsRestartBtn.disabled = false;
  }
}

function reconnectAllVisible() {
  // Reset failure backoff for all connections and reconnect visible cameras
  for (const path in state.connections) {
    const c = state.connections[path];
    c.failures = 0;
    if (c.retryTimer) { clearTimeout(c.retryTimer); c.retryTimer = null; }
  }
  // Re-render grid triggers fresh connections for visible cameras
  renderGrid();
}

// ===== AUTH ===================================================================

async function logout() {
  try {
    await fetch("/api/logout", { method: "POST" });
  } catch (_) {}
  location.href = "/login";
}

async function changePassword() {
  const cur = dom.settingsCurPw.value;
  const newPw = dom.settingsNewPw.value;
  if (!cur || !newPw) {
    dom.settingsChpwStatus.textContent = "Both fields required";
    dom.settingsChpwStatus.className = "err";
    return;
  }
  if (newPw.length < 4) {
    dom.settingsChpwStatus.textContent = "Min 4 characters";
    dom.settingsChpwStatus.className = "err";
    return;
  }
  try {
    const res = await fetch("/api/change-password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ current_password: cur, new_password: newPw }),
    });
    const data = await res.json();
    if (!res.ok) {
      dom.settingsChpwStatus.textContent = data.error || "Failed";
      dom.settingsChpwStatus.className = "err";
      return;
    }
    dom.settingsChpwStatus.textContent = "Password changed";
    dom.settingsChpwStatus.className = "ok";
    dom.settingsCurPw.value = "";
    dom.settingsNewPw.value = "";
  } catch (e) {
    dom.settingsChpwStatus.textContent = "Network error";
    dom.settingsChpwStatus.className = "err";
  }
}

// ===== INIT ===================================================================

async function init() {
  loadState();
  updateGridSizeInput();
  dom.patrolInterval.value = state.prefs.patrolInterval || 10;
  if (!state.prefs.sidebarOpen) dom.sidebar.classList.add("collapsed");

  renderLayoutSelect();
  bindEvents();
  setupKeyboard();

  await fetchInventory();
  await fetchCameras();

  if (state.prefs.lastLayout) {
    const layout = state.layouts.find(l => l.name === state.prefs.lastLayout);
    if (layout) loadLayout(layout.name);
  }

  setInterval(() => { fetchCameras(); scheduleStatusUpdate(); }, CONFIG.pollInterval);
}

document.addEventListener("DOMContentLoaded", init);

})();
