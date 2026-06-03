/**
 * DSS Camera Dashboard — entry point.
 *
 * Wires up event handlers, kicks off initial fetches, and starts the polling
 * + diagnostic timers. All feature code lives in sibling modules.
 */

import { CONFIG } from "./config.js";
import { state, loadState, savePrefs } from "./state.js";
import { dom } from "./dom.js";
import { dlog } from "./logger.js";
import { scheduleStatusUpdate } from "./utils.js";
import {
  fetchCameras, fetchInventory, startStallDetection, reconnectAllVisible,
  connectCamera,
} from "./streams.js";
import {
  renderSidebar, applyFilter, bindSidebarHooks,
  showGroupDialog, hideGroupDialog, saveGroupDialog,
  filterGroupCameras, toggleGroupSelectAll,
} from "./sidebar.js";
import {
  renderGrid, setGridSize, updateGridSizeInput, parseGridInput,
  autoFitGrid, goToPage, nextPage, prevPage, setFocusedCell,
  startPatrol, stopPatrol, togglePatrol, takeSnapshot, setupKeyboard,
} from "./grid.js";
import {
  openFullscreen, toggleFullscreenQuality, closeFullscreen,
} from "./fullscreen.js";
import {
  renderLayoutSelect, showLayoutDialog, hideLayoutDialog,
  saveLayout, loadLayout, deleteLayout,
} from "./layouts.js";
import { renderStatusPanel } from "./status-panel.js";
import {
  showContextMenu, hideContextMenu, toggleModal, toggleSidebar, initModals,
} from "./ui-common.js";
import {
  openSettings, addNvr, runHealth, runReconcile, refreshNvrs,
  addCamera, openEventsLog, changePasswordHandler, logout,
} from "./settings.js";

// ── Wire grid render into sidebar/streams via state hooks (avoid cyclic imports) ─

state._onCamerasChanged = () => { applyFilter(); renderSidebar(); };
state._onInventoryChanged = () => renderSidebar();
state._onGridDirty = () => renderGrid();
state._applyFilter = applyFilter;
state._goToPage = goToPage;
state._setFocusedCell = setFocusedCell;
bindSidebarHooks({ autoFitGrid, renderGrid });

// ── bindEvents ──────────────────────────────────────────────────────────────

function bindEvents() {
  dom.sidebarToggle.addEventListener("click", toggleSidebar);
  dom.showAllBtn.addEventListener("click", () => applyFilter("all", ""));

  dom.gridSizeSel.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); dom.gridSizeSel.blur(); }
  });
  dom.gridSizeSel.addEventListener("blur", () => {
    const parsed = parseGridInput(dom.gridSizeSel.value);
    if (parsed) setGridSize(parsed[0], parsed[1]);
    else updateGridSizeInput();
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
    if (e.key === "Enter") {
      const n = dom.layoutNameInput.value.trim();
      if (n) { saveLayout(n); hideLayoutDialog(); }
    }
  });

  dom.addGroupBtn.addEventListener("click", () => showGroupDialog());
  dom.groupCreateBtn.addEventListener("click", saveGroupDialog);
  dom.groupCancelBtn.addEventListener("click", hideGroupDialog);
  dom.groupNameInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") saveGroupDialog();
  });
  dom.groupCamSearch.addEventListener("input", () => filterGroupCameras(dom.groupCamSearch.value));
  dom.groupSelectAll.addEventListener("click", toggleGroupSelectAll);

  dom.statusToggle.addEventListener("click", () => { renderStatusPanel(); toggleModal(dom.statusPanel); });
  dom.reconnectAllBtn.addEventListener("click", () => {
    for (const path in state.connections) {
      const c = state.connections[path];
      if (c.status === "error" && c.video) connectCamera(path, c.video);
    }
  });

  dom.shortcutsBtn.addEventListener("click", () => toggleModal(dom.shortcutsModal));
  dom.fsCloseBtn.addEventListener("click", closeFullscreen);
  dom.fsQualityBtn.addEventListener("click", toggleFullscreenQuality);
  dom.fsSnapshotBtn.addEventListener("click", () => {
    if (state.fullscreenPath) takeSnapshot(state.fullscreenPath, dom.fsVideo);
  });

  // Settings / admin modal
  dom.settingsBtn.addEventListener("click", openSettings);
  dom.settingsAddBtn.addEventListener("click", addNvr);
  dom.settingsHealthBtn.addEventListener("click", runHealth);
  dom.settingsReconcileBtn.addEventListener("click", runReconcile);
  dom.settingsRefreshBtn.addEventListener("click", refreshNvrs);
  dom.settingsEventsBtn.addEventListener("click", openEventsLog);
  dom.camerasAddBtn.addEventListener("click", addCamera);
  dom.settingsAdvToggle.addEventListener("click", () => {
    const open = dom.settingsAdvPanel.classList.toggle("hidden");
    dom.settingsAdvToggle.setAttribute("aria-expanded", String(!open));
    dom.settingsAdvToggle.textContent = open ? "Advanced ▾" : "Advanced ▴";
  });

  // Password-field eyes inside the modal (add-form + change-password section).
  document.querySelectorAll("#settings-modal .pw-toggle").forEach(btn => {
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      const inp = btn.previousElementSibling;
      if (inp) inp.type = inp.type === "password" ? "text" : "password";
    });
  });

  // Auth
  dom.logoutBtn.addEventListener("click", logout);
  dom.settingsChpwBtn.addEventListener("click", changePasswordHandler);
}

// ── Init ────────────────────────────────────────────────────────────────────

async function init() {
  loadState();
  updateGridSizeInput();
  dom.patrolInterval.value = state.prefs.patrolInterval || 10;
  if (!state.prefs.sidebarOpen) dom.sidebar.classList.add("collapsed");

  renderLayoutSelect();
  initModals();
  bindEvents();
  setupKeyboard({
    closeFullscreen,
    hideGroupDialog,
    hideLayoutDialog,
    hideContextMenu,
    toggleFullscreenQuality,
    showGroupDialog,
    openSettings,
  });

  await fetchInventory();
  await fetchCameras();
  applyFilter();
  renderSidebar();

  if (state.prefs.lastLayout) {
    const layout = state.layouts.find(l => l.name === state.prefs.lastLayout);
    if (layout) loadLayout(layout.name);
  }

  setInterval(() => { fetchCameras(); scheduleStatusUpdate(); }, CONFIG.pollInterval);
  startStallDetection();

  // Periodic connection summary for diagnostics
  setInterval(() => {
    let online = 0, connecting = 0, errored = 0, total = 0;
    const errPaths = [];
    for (const p in state.connections) {
      total++;
      const c = state.connections[p];
      if (c.status === "live") online++;
      else if (c.status === "connecting") connecting++;
      else if (c.status === "error") {
        errored++;
        if (errPaths.length < 10) errPaths.push(`${p}(${c.lastError||"?"},f=${c.failures})`);
      }
    }
    if (total > 0) {
      dlog.info("", "status-summary", `online=${online} connecting=${connecting} error=${errored} total=${total}` +
        (errPaths.length > 0 ? ` errs=[${errPaths.join(", ")}]` : ""));
    }
  }, 30000);

  window.addEventListener("beforeunload", () => dlog.flush());

  dlog.info("", "init-complete", `cameras=${state.allCameras.length} grid=${state.gridCols}x${state.gridRows}`);
}

document.addEventListener("DOMContentLoaded", init);
