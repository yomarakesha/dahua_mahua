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
  pollInterval: 10000,
  maxConcurrent: 4,        // max simultaneous WebRTC negotiations
  reconnectBase: 2000,     // base reconnect delay (ms)
  reconnectMax: 30000,     // max reconnect delay (ms)
  gridSizes: [2, 4, 8, 16, 32, 64],
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
  gridSize: 4,
  currentPage: 0,
  searchText: "",
  activeFilter: { type: "all", value: "" },
  groups: [],
  layouts: [],
  prefs: { gridSize: 4, patrolInterval: 10, sidebarOpen: true, lastLayout: "" },
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
  while (connQueue.active < CONFIG.maxConcurrent && connQueue.pending.length > 0) {
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
    state.gridSize = state.prefs.gridSize || 4;
  } catch (_) {}
}

function saveGroups()  { localStorage.setItem(LS.groups, JSON.stringify(state.groups)); }
function saveLayouts() { localStorage.setItem(LS.layouts, JSON.stringify(state.layouts)); }
function savePrefs()   { localStorage.setItem(LS.prefs, JSON.stringify(state.prefs)); }

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
  return nvrs;
}

function totalPages() {
  const perPage = state.gridSize * state.gridSize;
  return Math.max(1, Math.ceil(state.filteredCameras.length / perPage));
}

function getPageCameras() {
  const perPage = state.gridSize * state.gridSize;
  const start = state.currentPage * perPage;
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
    const res = await fetch(`${CONFIG.apiBase}/paths/list`);
    if (!res.ok) throw new Error(`API ${res.status}`);
    const data = await res.json();
    const items = data.items || data;
    const paths = items.map(i => i.name).filter(n => !n.endsWith("_main")).sort();
    if (JSON.stringify(paths) !== JSON.stringify(state.allCameras)) {
      state.allCameras = paths;
      applyFilter();
      renderSidebar();
    }
  } catch (_) {}
}

async function fetchInventory() {
  try {
    const res = await fetch("/api/inventory");
    if (res.ok) state.inventory = await res.json();
  } catch (_) {}
}

// ===== WEBRTC =================================================================

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
  };
  updateCellDot(path, "connecting");
  scheduleStatusUpdate();
  queueConnection(path, videoEl);
}

// Actual WebRTC negotiation — called from queue, returns promise.
async function doConnect(path, videoEl) {
  const conn = state.connections[path];
  if (!conn) return;

  const pc = new RTCPeerConnection({
    iceServers: [{ urls: "stun:stun.l.google.com:19302" }],
  });
  conn.pc = pc;

  // VIDEO ONLY — no audio transceiver = significant bandwidth savings
  pc.addTransceiver("video", { direction: "recvonly" });

  pc.ontrack = (evt) => {
    if (state.connections[path] && state.connections[path].pc === pc) {
      videoEl.srcObject = evt.streams[0];
      conn.status = "live";
      conn.failures = 0;  // reset backoff on success
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
      scheduleReconnect(path, videoEl, pc);
    }
  };

  try {
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    const res = await fetch(`${CONFIG.webrtcBase}/${path}/whep`, {
      method: "POST",
      headers: { "Content-Type": "application/sdp" },
      body: pc.localDescription.sdp,
    });
    if (!res.ok) throw new Error(`WHEP ${res.status}`);
    const answer = await res.text();
    await pc.setRemoteDescription({ type: "answer", sdp: answer });
  } catch (_) {
    if (state.connections[path] && state.connections[path].pc === pc) {
      state.connections[path].status = "error";
      updateCellDot(path, "error");
      scheduleStatusUpdate();
      scheduleReconnect(path, videoEl, pc);
    }
  }
}

// Exponential backoff reconnect
function scheduleReconnect(path, videoEl, oldPc) {
  const conn = state.connections[path];
  if (!conn || conn.pc !== oldPc) return;

  conn.failures = (conn.failures || 0) + 1;
  // Exponential backoff: 2s, 4s, 8s, 16s... capped at 30s
  const delay = Math.min(CONFIG.reconnectBase * Math.pow(2, conn.failures - 1), CONFIG.reconnectMax);

  conn.retryTimer = setTimeout(() => {
    if (state.connections[path] && state.connections[path].pc === oldPc) {
      connectCamera(path, videoEl);
    }
  }, delay);
}

function disconnectCamera(path) {
  const entry = state.connections[path];
  if (entry) {
    if (entry.retryTimer) clearTimeout(entry.retryTimer);
    if (entry.pc) { try { entry.pc.close(); } catch(_){} }
    if (entry.video) entry.video.srcObject = null;
    delete state.connections[path];
  }
}

function disconnectAllNotVisible(visibleSet) {
  for (const p in state.connections) {
    if (!visibleSet.has(p)) disconnectCamera(p);
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
  const tp = totalPages();
  if (state.currentPage >= tp) state.currentPage = tp - 1;
  if (state.currentPage < 0) state.currentPage = 0;

  renderGrid();
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
    goToPage(Math.floor(idx2 / (state.gridSize * state.gridSize)));
    return;
  }
  const page = Math.floor(idx / (state.gridSize * state.gridSize));
  if (page !== state.currentPage) goToPage(page);
  setFocusedCell(idx % (state.gridSize * state.gridSize));
}

// ===== GRID RENDERING =========================================================

function renderGrid() {
  const pageCams = getPageCameras();
  const g = state.gridSize;
  const visibleSet = new Set(pageCams);

  // Flush pending queue — don't start connections for cameras we're about to leave
  flushQueue();

  // Disconnect cameras not on this page
  disconnectAllNotVisible(visibleSet);

  // Set grid CSS
  dom.cameraGrid.style.gridTemplateColumns = `repeat(${g}, 1fr)`;
  dom.cameraGrid.style.gridTemplateRows = `repeat(${g}, 1fr)`;
  dom.cameraGrid.className = `grid-${g}`;

  // Build DOM in fragment to avoid reflows
  const frag = document.createDocumentFragment();
  const totalSlots = g * g;

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
      setupZoom(cell, video, i);

      // Queue connection (respects maxConcurrent)
      connectCamera(path, video);
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
    gridSize: state.gridSize,
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
  state.gridSize = layout.gridSize || 4;
  state.customOrder = layout.cameraOrder ? [...layout.cameraOrder] : null;
  dom.gridSizeSel.value = state.gridSize;
  state.prefs.gridSize = state.gridSize;
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
  const perPage = state.gridSize * state.gridSize;
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

// ===== DIGITAL ZOOM ===========================================================

function setupZoom(cell, video, index) {
  let scale = 1, panX = 0, panY = 0;
  let isPanning = false, lastX = 0, lastY = 0;

  cell.addEventListener("wheel", (e) => {
    e.preventDefault();
    scale = Math.max(1, Math.min(10, scale + (e.deltaY > 0 ? -0.3 : 0.3)));
    if (scale === 1) { panX = 0; panY = 0; }
    applyTransform();
  }, { passive: false });

  cell.addEventListener("mousedown", (e) => {
    if (scale <= 1 || e.button !== 0) return;
    isPanning = true;
    lastX = e.clientX;
    lastY = e.clientY;
    cell.style.cursor = "grabbing";
    e.preventDefault();
  });

  const onMove = (e) => {
    if (!isPanning) return;
    panX += e.clientX - lastX;
    panY += e.clientY - lastY;
    lastX = e.clientX;
    lastY = e.clientY;
    applyTransform();
  };

  const onUp = () => {
    if (isPanning) { isPanning = false; cell.style.cursor = ""; }
  };

  // Use cell-scoped listeners to avoid N global listeners
  cell.addEventListener("mousemove", onMove);
  cell.addEventListener("mouseup", onUp);
  cell.addEventListener("mouseleave", onUp);

  video.addEventListener("dblclick", (e) => {
    if (scale > 1) {
      e.stopPropagation();
      scale = 1; panX = 0; panY = 0;
      applyTransform();
    }
  });

  function applyTransform() {
    video.style.transform = scale > 1 ? `scale(${scale}) translate(${panX / scale}px, ${panY / scale}px)` : "";
    let indicator = cell.querySelector(".zoom-indicator");
    if (scale > 1) {
      if (!indicator) { indicator = document.createElement("div"); indicator.className = "zoom-indicator"; cell.appendChild(indicator); }
      indicator.textContent = scale.toFixed(1) + "x";
    } else if (indicator) {
      indicator.remove();
    }
  }
}

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
      setGridSize(CONFIG.gridSizes[parseInt(e.key) - 1]);
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
  // Show sub-stream immediately as preview
  if (conn && conn.video && conn.video.srcObject) {
    dom.fsVideo.srcObject = conn.video.srcObject;
  }
  dom.fsOverlay.classList.remove("hidden");
  // Connect to main-stream for full quality
  connectFullscreenMain(path);
}

async function connectFullscreenMain(path) {
  disconnectFullscreenMain();
  const mainPath = path + "_main";
  const pc = new RTCPeerConnection({
    iceServers: [{ urls: "stun:stun.l.google.com:19302" }],
  });
  state.fullscreenConn = { pc };

  pc.addTransceiver("video", { direction: "recvonly" });

  pc.ontrack = (evt) => {
    if (state.fullscreenConn && state.fullscreenConn.pc === pc) {
      dom.fsVideo.srcObject = evt.streams[0];
    }
  };

  try {
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    const res = await fetch(`${CONFIG.webrtcBase}/${mainPath}/whep`, {
      method: "POST",
      headers: { "Content-Type": "application/sdp" },
      body: pc.localDescription.sdp,
    });
    if (!res.ok) throw new Error(`WHEP ${res.status}`);
    const answer = await res.text();
    await pc.setRemoteDescription({ type: "answer", sdp: answer });
  } catch (_) {
    // Main stream unavailable — keep showing sub-stream preview
  }
}

function disconnectFullscreenMain() {
  if (state.fullscreenConn) {
    try { state.fullscreenConn.pc.close(); } catch (_) {}
    state.fullscreenConn = null;
  }
}

function closeFullscreen() {
  disconnectFullscreenMain();
  dom.fsOverlay.classList.add("hidden");
  dom.fsVideo.srcObject = null;
  state.fullscreenPath = null;
}

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

function setGridSize(size) {
  state.gridSize = size;
  state.prefs.gridSize = size;
  dom.gridSizeSel.value = size;
  state.currentPage = 0;
  savePrefs();
  renderGrid();
}

// ===== EVENT BINDINGS =========================================================

function bindEvents() {
  dom.sidebarToggle.addEventListener("click", toggleSidebar);
  dom.showAllBtn.addEventListener("click", () => applyFilter("all", ""));
  dom.gridSizeSel.addEventListener("change", () => setGridSize(parseInt(dom.gridSizeSel.value)));

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
}

// ===== NVR SETTINGS ===========================================================

function openSettings() {
  setSettingsStatus("");
  fetch("/api/inventory")
    .then(r => r.json())
    .then(inv => {
      renderSettingsForm(inv);
      dom.settingsModal.classList.remove("hidden");
    })
    .catch(e => alert("Failed to load inventory: " + e));
}

function renderSettingsForm(inv) {
  const g = inv.global || {};
  dom.settingsPort.value = g.default_port || 554;
  dom.settingsUser.value = g.default_username || "";
  dom.settingsPass.value = g.default_password || "";
  dom.settingsSubtype.value = g.default_subtype != null ? g.default_subtype : 1;

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
    `<td><input type="password" value="${esc(nvr.password || "")}" placeholder="(global)" data-field="password"></td>` +
    `<td><button class="settings-row-btn del" title="Remove">&times;</button></td>`;

  // Delete button
  tr.querySelector(".del").addEventListener("click", () => { tr.remove(); updateNvrCount(); });

  // Double-click to reveal password
  const passInput = tr.querySelector('[data-field="password"]');
  passInput.addEventListener("dblclick", () => { passInput.type = "text"; });
  passInput.addEventListener("blur", () => { passInput.type = "password"; });

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

async function saveSettings() {
  const inv = harvestInventory();
  dom.settingsSaveBtn.disabled = true;
  setSettingsStatus("Saving...");
  try {
    const res = await fetch("/api/inventory", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(inv),
    });
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

// ===== INIT ===================================================================

async function init() {
  loadState();
  dom.gridSizeSel.value = state.gridSize;
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
