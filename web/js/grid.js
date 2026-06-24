/**
 * Camera grid: render, navigation, sizing, drag-drop, patrol, keyboard, snapshot.
 */

import { CONFIG } from "./config.js";
import { state, savePrefs } from "./state.js";
import { dom } from "./dom.js";
import { dlog } from "./logger.js";
import {
  labelFor, gridCells, totalPages, getPageCameras, getNextPageCameras,
  getPreconnectLimit, scheduleStatusUpdate,
} from "./utils.js";
import {
  connectCamera, attachConnectionVideo, disconnectAllNotVisible,
  flushQueue, updateCellDot, streamPathFor,
} from "./streams.js";
import { showContextMenu, toggleSidebar, toggleModal } from "./ui-common.js";
import { addToGroup } from "./sidebar.js";
import { openFullscreen } from "./fullscreen.js";

// ── Grid rendering ──────────────────────────────────────────────────────────
// Two paths: full rebuild (size change → recreate every cell) and diff-update
// (same size → reuse cell DIVs and their event listeners; only replace inner
// contents when the path actually changes). The latter avoids the cost of
// `innerHTML = ""` + 64 fresh listener bindings on every page-flip / filter.

// ── Cold-start throttle ──────────────────────────────────────────────────────
// Each fresh tile opens one on-demand RTSP pull from the NVR. The NVR caps
// concurrent pulls (~12-16), so firing a whole grid at once makes the tail wait
// ~25s+ and the browser's WHEP request times out. Launch *new* connections in
// small staggered batches; already-connected (warm) tiles re-attach instantly
// and never enter this queue.
const COLD_BATCH = 4;
const COLD_GAP_MS = 2000;
const _coldQueue = [];   // [{ path, getVideo }]
let _coldTimer = null;

function liveVideoFor(path) {
  const sel = `.cam-cell[data-path="${(window.CSS && CSS.escape) ? CSS.escape(path) : path}"]`;
  const cell = dom.cameraGrid.querySelector(sel);
  return cell ? cell.querySelector("video") : null;
}

function drainColdQueue() {
  _coldTimer = null;
  let launched = 0;
  while (_coldQueue.length && launched < COLD_BATCH) {
    const { path, getVideo, preconnect } = _coldQueue.shift();
    const video = getVideo();
    if (!video) continue;                       // tile gone (page flip) — drop it
    const conn = state.connections[path];
    if (conn && conn.status !== "error" && conn.pc) {
      attachConnectionVideo(path, video);       // became warm meanwhile — no NVR hit
    } else {
      connectCamera(path, video);
      if (preconnect) {
        const c = state.connections[path];
        if (c) c.preconnected = true;
      }
      launched++;
    }
  }
  if (_coldQueue.length) _coldTimer = setTimeout(drainColdQueue, COLD_GAP_MS);
}

function queueColdConnect(path, getVideo, preconnect = false) {
  if (_coldQueue.some(q => q.path === path)) return;
  _coldQueue.push({ path, getVideo, preconnect });
  if (!_coldTimer) _coldTimer = setTimeout(drainColdQueue, 0); // first batch ASAP
}

function attachOrConnect(path, video) {
  const existingConn = state.connections[path];
  const desiredStream = streamPathFor(path);
  if (existingConn) {
    // Tier mismatch (sub↔main) — force a reconnect on the right path.
    if (existingConn.streamPath && existingConn.streamPath !== desiredStream) {
      queueColdConnect(path, () => liveVideoFor(path) || video);
      return;
    }
    attachConnectionVideo(path, video);
    if (existingConn.status === "error") {
      if (existingConn.retryTimer) {
        clearTimeout(existingConn.retryTimer);
        existingConn.retryTimer = null;
      }
      queueColdConnect(path, () => liveVideoFor(path) || video);
    }
  } else {
    queueColdConnect(path, () => liveVideoFor(path) || video);
  }
}

// Bound once per cell at creation. Listeners read cell.dataset.path/index live,
// so they survive the cell being repurposed for a different path on diff-update.
function bindCellListeners(cell) {
  cell.addEventListener("click", () => {
    const idx = parseInt(cell.dataset.index, 10);
    if (!Number.isNaN(idx)) setFocusedCell(idx);
  });
  cell.addEventListener("dblclick", () => {
    const path = cell.dataset.path;
    if (path) openFullscreen(path);
  });
  cell.addEventListener("contextmenu", (e) => {
    e.preventDefault();
    const path = cell.dataset.path;
    if (!path) return;
    const video = cell.querySelector("video");
    const items = [
      { label: "Fullscreen", action: () => openFullscreen(path) },
      { label: "Snapshot", action: () => takeSnapshot(path, video) },
      { label: "Reconnect", action: () => connectCamera(path, video) },
      { type: "separator" },
    ];
    state.groups.forEach(grp => {
      const inGroup = grp.cameras.includes(path);
      items.push({
        label: (inGroup ? "✓ " : "  ") + grp.name,
        action: () => { if (!inGroup) addToGroup(grp.name, path); },
      });
    });
    showContextMenu(e, items);
  });

  cell.addEventListener("dragstart", (e) => {
    const path = cell.dataset.path;
    if (!path) return;
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
    const idx = parseInt(cell.dataset.index, 10);
    if (cam && !Number.isNaN(idx)) handleDrop(cam, idx);
  });
}

function populatePathCell(cell, path) {
  while (cell.firstChild) cell.removeChild(cell.firstChild);
  cell.dataset.path = path;
  cell.draggable = true;
  cell.style.background = "";

  const video = document.createElement("video");
  video.autoplay = true;
  video.muted = true;
  video.playsInline = true;

  const dot = document.createElement("div");
  dot.className = "status-dot";
  dot.dataset.dotPath = path;

  const label = document.createElement("div");
  label.className = "label";
  label.textContent = labelFor(path);

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

  attachOrConnect(path, video);
}

function populateEmptyCell(cell) {
  while (cell.firstChild) cell.removeChild(cell.firstChild);
  delete cell.dataset.path;
  cell.draggable = false;
  cell.style.background = "#0a0a0a";
}

export function renderGrid() {
  const pageCams = getPageCameras();
  // Auto mode: size the grid to exactly fit this page's cameras (the page was
  // already capped to a safe per-NVR count in buildPages).
  if (state.autoGrid) {
    const [c, r] = fitDims(pageCams.length || 1);
    state.gridCols = c;
    state.gridRows = r;
  }
  const cols = state.gridCols;
  const rows = state.gridRows;
  const totalSlots = cols * rows;
  const visibleSet = new Set(pageCams);
  const nextCams = getNextPageCameras();
  const preconnectCams = nextCams.slice(0, getPreconnectLimit());
  const preconnectSet = new Set(preconnectCams);

  flushQueue();
  disconnectAllNotVisible(visibleSet, preconnectSet);

  const colsTpl = `repeat(${cols}, 1fr)`;
  const rowsTpl = `repeat(${rows}, 1fr)`;
  const needFullRebuild = (
    dom.cameraGrid.children.length !== totalSlots ||
    dom.cameraGrid.style.gridTemplateColumns !== colsTpl ||
    dom.cameraGrid.style.gridTemplateRows !== rowsTpl
  );

  const t0 = performance.now();
  if (needFullRebuild) {
    dom.cameraGrid.style.gridTemplateColumns = colsTpl;
    dom.cameraGrid.style.gridTemplateRows = rowsTpl;
    dom.cameraGrid.className = `grid-${Math.max(cols, rows)}`;

    const frag = document.createDocumentFragment();
    for (let i = 0; i < totalSlots; i++) {
      const cell = document.createElement("div");
      cell.className = "cam-cell";
      cell.dataset.index = i;
      bindCellListeners(cell);
      if (pageCams[i]) populatePathCell(cell, pageCams[i]);
      else populateEmptyCell(cell);
      frag.appendChild(cell);
    }
    dom.cameraGrid.innerHTML = "";
    dom.cameraGrid.appendChild(frag);
    dlog.info("", "grid-render-full",
      `cells=${totalSlots} cams=${pageCams.filter(Boolean).length} ` +
      `grid=${cols}x${rows} page=${state.currentPage + 1}/${totalPages()} ` +
      `dt=${(performance.now() - t0).toFixed(1)}ms`);
  } else {
    const cells = dom.cameraGrid.children;
    let changed = 0, kept = 0;
    for (let i = 0; i < totalSlots; i++) {
      const cell = cells[i];
      const oldPath = cell.dataset.path || null;
      const newPath = pageCams[i] || null;
      if (oldPath === newPath) {
        if (newPath) {
          const video = cell.querySelector("video");
          const label = cell.querySelector(".label");
          const newLabel = labelFor(newPath);
          if (label && label.textContent !== newLabel) label.textContent = newLabel;
          if (video) attachOrConnect(newPath, video);
          kept++;
        }
        continue;
      }
      if (newPath) populatePathCell(cell, newPath);
      else populateEmptyCell(cell);
      changed++;
    }
    dlog.info("", "grid-render-diff",
      `cells=${totalSlots} kept=${kept} changed=${changed} ` +
      `page=${state.currentPage + 1}/${totalPages()} ` +
      `dt=${(performance.now() - t0).toFixed(1)}ms`);
  }

  const tp = totalPages();
  dom.sbPage.textContent = tp > 1 ? `Page ${state.currentPage + 1}/${tp}` : "";
  dom.sbTotal.textContent = `Total: ${state.filteredCameras.length}`;
  state.focusedCell = -1;

  if (tp > 1 && preconnectCams.length > 0) {
    clearTimeout(state._preconnectTimer);
    state._preconnectTimer = setTimeout(() => preconnectNextPage(preconnectCams), 2000);
  }
}

function preconnectNextPage(paths) {
  const nextCams = Array.isArray(paths) ? paths : getNextPageCameras().slice(0, getPreconnectLimit());
  if (nextCams.length === 0) return;

  nextCams.forEach(path => {
    if (!path) return;
    if (state.connections[path]) return;

    const video = document.createElement("video");
    video.autoplay = true;
    video.muted = true;
    video.playsInline = true;
    video.style.display = "none";
    document.body.appendChild(video);

    // Share the cold-start throttle so preconnect never bursts the NVR either.
    queueColdConnect(path, () => video, true);
  });
}

export function setFocusedCell(index) {
  dom.cameraGrid.querySelectorAll(".cam-cell.focused").forEach(el => el.classList.remove("focused"));
  state.focusedCell = index;
  const cells = dom.cameraGrid.querySelectorAll(".cam-cell");
  if (cells[index]) cells[index].classList.add("focused");
}

export function getFocusedPath() {
  if (state.focusedCell < 0) return null;
  return getPageCameras()[state.focusedCell] || null;
}

// ── Navigation ──────────────────────────────────────────────────────────────

export function goToPage(page) {
  const tp = totalPages();
  if (page < 0) page = tp - 1;
  if (page >= tp) page = 0;
  if (page === state.currentPage) return;
  state.currentPage = page;
  renderGrid();
}

export function nextPage() { goToPage(state.currentPage + 1); }
export function prevPage() { goToPage(state.currentPage - 1); }

// ── Grid sizing ─────────────────────────────────────────────────────────────

// Square-ish [cols, rows] that fits n tiles (n=12 → 4x3, 16 → 4x4, 9 → 3x3).
function fitDims(n) {
  const c = Math.ceil(Math.sqrt(n));
  const r = Math.ceil(n / c);
  return [c, r];
}

// Manual size = explicit fixed grid → leaves auto mode.
export function setGridSize(cols, rows) {
  state.autoGrid = false;
  state.prefs.autoGrid = false;
  state.gridCols = cols;
  state.gridRows = rows || cols;
  state.prefs.gridCols = state.gridCols;
  state.prefs.gridRows = state.gridRows;
  updateGridSizeInput();
  state.currentPage = 0;
  savePrefs();
  renderGrid();
}

export function setAutoGrid(on) {
  state.autoGrid = !!on;
  state.prefs.autoGrid = state.autoGrid;
  state.currentPage = 0;
  savePrefs();
  updateGridSizeInput();
  renderGrid();
}

export function updateGridSizeInput() {
  dom.gridSizeSel.value = state.autoGrid ? "auto" : `${state.gridCols}x${state.gridRows}`;
}

export function parseGridInput(val) {
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

export function autoFitGrid(cameraCount) {
  // In auto mode the render already fits each (NVR-capped) page, so just redraw.
  if (state.autoGrid) { state.currentPage = 0; renderGrid(); return; }
  if (cameraCount <= 0) return;
  const s = Math.ceil(Math.sqrt(cameraCount));
  const rows = Math.ceil(cameraCount / s);
  setGridSize(s, rows);
}

// ── Drag-and-drop reorder ───────────────────────────────────────────────────

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
  // applyFilter rebuilds and rerenders
  if (state._applyFilter) state._applyFilter();
}

// ── Snapshot ────────────────────────────────────────────────────────────────

export function takeSnapshot(path, videoEl) {
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

// ── Patrol mode ─────────────────────────────────────────────────────────────

export function startPatrol() {
  if (state.patrol.active) return;
  state.patrol.active = true;
  state.patrol.paused = false;
  const interval = parseInt(dom.patrolInterval.value) || 10;
  state.patrol.countdown = interval;

  dom.patrolBtn.classList.add("active");
  dom.patrolBtn.innerHTML = "■ Stop";

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

export function stopPatrol() {
  state.patrol.active = false;
  state.patrol.paused = false;
  if (state.patrol.timer) { clearInterval(state.patrol.timer); state.patrol.timer = null; }
  dom.patrolBtn.classList.remove("active");
  dom.patrolBtn.innerHTML = "▶ Patrol";
  dom.patrolCountdown.textContent = "";
}

export function togglePatrol() {
  if (state.patrol.active) stopPatrol(); else startPatrol();
}

// ── Keyboard shortcuts ──────────────────────────────────────────────────────

export function setupKeyboard(handlers) {
  document.addEventListener("keydown", (e) => {
    const tag = e.target.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") {
      if (e.key === "Escape") e.target.blur();
      return;
    }

    if (e.key === "Escape") {
      if (state.fullscreenPath) { handlers.closeFullscreen(); return; }
      if (!dom.statusPanel.classList.contains("hidden")) { dom.statusPanel.classList.add("hidden"); return; }
      if (!dom.shortcutsModal.classList.contains("hidden")) { dom.shortcutsModal.classList.add("hidden"); return; }
      if (!dom.settingsModal.classList.contains("hidden")) { dom.settingsModal.classList.add("hidden"); return; }
      if (!dom.groupDialog.classList.contains("hidden")) { handlers.hideGroupDialog(); return; }
      if (!dom.layoutDialog.classList.contains("hidden")) { handlers.hideLayoutDialog(); return; }
      handlers.hideContextMenu();
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
    if (e.key === " ")          { togglePatrol(); e.preventDefault(); return; }

    if (e.key === "f" || e.key === "F") {
      const p = getFocusedPath();
      if (p) openFullscreen(p);
      e.preventDefault();
      return;
    }

    if ((e.key === "q" || e.key === "Q") && state.fullscreenPath) {
      handlers.toggleFullscreenQuality();
      e.preventDefault();
      return;
    }

    if ((e.key === "n" || e.key === "N") && state.fullscreenPath) {
      handlers.toggleFullscreenSource();
      e.preventDefault();
      return;
    }

    if ((e.key === "m" || e.key === "M") && state.fullscreenPath) {
      handlers.toggleFullscreenSound();
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
    if (e.key === "g" || e.key === "G") { handlers.showGroupDialog(); e.preventDefault(); return; }
    if (e.key === ",") { handlers.openSettings(); e.preventDefault(); return; }
    if (e.key === "Tab") { toggleSidebar(); e.preventDefault(); return; }
  });
}

// Mouse-hover pauses patrol cycle on the grid
dom.gridContainer.addEventListener("mouseenter", () => { if (state.patrol.active) state.patrol.paused = true; });
dom.gridContainer.addEventListener("mouseleave", () => { if (state.patrol.active) state.patrol.paused = false; });
