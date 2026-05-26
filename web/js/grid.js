/**
 * Camera grid: render, navigation, sizing, drag-drop, patrol, keyboard, snapshot.
 */

import { CONFIG } from "./config.js";
import { state, savePrefs } from "./state.js";
import { dom } from "./dom.js";
import {
  labelFor, gridCells, totalPages, getPageCameras, getNextPageCameras,
  getPreconnectLimit, scheduleStatusUpdate,
} from "./utils.js";
import {
  connectCamera, attachConnectionVideo, disconnectAllNotVisible,
  flushQueue, updateCellDot,
} from "./streams.js";
import { showContextMenu, toggleSidebar, toggleModal } from "./ui-common.js";
import { addToGroup } from "./sidebar.js";
import { openFullscreen } from "./fullscreen.js";

// ── Grid rendering ──────────────────────────────────────────────────────────

export function renderGrid() {
  const pageCams = getPageCameras();
  const cols = state.gridCols;
  const rows = state.gridRows;
  const visibleSet = new Set(pageCams);
  const nextCams = getNextPageCameras();
  const preconnectCams = nextCams.slice(0, getPreconnectLimit());
  const preconnectSet = new Set(preconnectCams);

  flushQueue();
  disconnectAllNotVisible(visibleSet, preconnectSet);

  dom.cameraGrid.style.gridTemplateColumns = `repeat(${cols}, 1fr)`;
  dom.cameraGrid.style.gridTemplateRows = `repeat(${rows}, 1fr)`;
  dom.cameraGrid.className = `grid-${Math.max(cols, rows)}`;

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
            label: (inGroup ? "✓ " : "  ") + grp.name,
            action: () => { if (!inGroup) addToGroup(grp.name, path); },
          });
        });
        showContextMenu(e, items);
      });

      cell.draggable = true;
      setupDragDrop(cell, path, i);

      const existingConn = state.connections[path];
      if (existingConn) {
        attachConnectionVideo(path, video);
        if (existingConn.status === "error") {
          if (existingConn.retryTimer) {
            clearTimeout(existingConn.retryTimer);
            existingConn.retryTimer = null;
          }
          connectCamera(path, video);
        }
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

    connectCamera(path, video);
    const conn = state.connections[path];
    if (conn) conn.preconnected = true;
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

export function setGridSize(cols, rows) {
  state.gridCols = cols;
  state.gridRows = rows || cols;
  state.prefs.gridCols = state.gridCols;
  state.prefs.gridRows = state.gridRows;
  updateGridSizeInput();
  state.currentPage = 0;
  savePrefs();
  renderGrid();
}

export function updateGridSizeInput() {
  dom.gridSizeSel.value = `${state.gridCols}x${state.gridRows}`;
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
  if (cameraCount <= 0) return;
  const s = Math.ceil(Math.sqrt(cameraCount));
  const rows = Math.ceil(cameraCount / s);
  setGridSize(s, rows);
}

// ── Drag-and-drop reorder ───────────────────────────────────────────────────

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
