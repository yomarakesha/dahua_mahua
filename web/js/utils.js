/**
 * Shared utilities: path parsing, label formatting, toast notifications,
 * batched status-bar updates, warning banner.
 */

import { state } from "./state.js";
import { dom } from "./dom.js";

// ── Path parsing ────────────────────────────────────────────────────────────

export function getNvrId(path)   { return path.split("_")[0]; }
export function getChannel(path) { return path.split("_").slice(1).join("_"); }
export function formatName(path) { return path.replace(/_/g, " / ").toUpperCase(); }

// Human-readable label: uses NVR label from inventory + channel id.
// Falls back to formatName when inventory not yet loaded.
export function labelFor(path) {
  if (!state.inventory || !state.inventory.nvrs) return formatName(path);
  const nvrId = getNvrId(path);
  const nvr = state.inventory.nvrs.find(n => n.id === nvrId);
  if (!nvr || !nvr.label) return formatName(path);
  return `${nvr.label} / ${getChannel(path).toUpperCase()}`;
}

export function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, ch => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

// ── Camera grouping & pagination ────────────────────────────────────────────

export function getNvrList() {
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

export function gridCells() { return state.gridCols * state.gridRows; }

export function totalPages() {
  return Math.max(1, Math.ceil(state.filteredCameras.length / gridCells()));
}

export function getPageCameras() {
  const perPage = gridCells();
  const start = state.currentPage * perPage;
  return state.filteredCameras.slice(start, start + perPage);
}

export function getNextPageCameras() {
  const tp = totalPages();
  if (tp <= 1) return [];
  const nextPage = (state.currentPage + 1) % tp;
  const perPage = gridCells();
  const start = nextPage * perPage;
  return state.filteredCameras.slice(start, start + perPage);
}

export function getPreconnectLimit() {
  const cells = gridCells();
  let base = 0;
  if (cells <= 4) base = state.patrol.active ? 4 : 2;
  else if (cells <= 9) base = state.patrol.active ? 2 : 1;
  else if (cells <= 16) base = 1;
  return Math.max(0, Math.min(base, Math.max(0, state.prefs.maxConcurrent - 1)));
}

// ── Toast notifications ─────────────────────────────────────────────────────

export function showToast(message, type = "", duration = 6000) {
  const el = document.createElement("div");
  el.className = "toast" + (type ? " " + type : "");
  el.textContent = message;
  dom.toastContainer.appendChild(el);
  setTimeout(() => {
    el.classList.add("toast-fade");
    setTimeout(() => el.remove(), 400);
  }, duration);
}

// ── Warning banner ──────────────────────────────────────────────────────────

export function showWarning(msg) {
  if (msg) {
    dom.warningBanner.textContent = msg;
    dom.warningBanner.classList.remove("hidden");
  } else {
    dom.warningBanner.classList.add("hidden");
  }
}

// ── Batched status updates (coalesce per frame) ─────────────────────────────

let statusDirty = false;

export function scheduleStatusUpdate() {
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
