/**
 * Shared mutable state + localStorage persistence.
 */

import { LS } from "./config.js";

export const state = {
  allCameras: [],
  filteredCameras: [],
  gridCols: 4,
  gridRows: 4,
  currentPage: 0,
  searchText: "",
  activeFilter: { type: "all", value: "" },
  groups: [],
  layouts: [],
  prefs: {
    gridCols: 4, gridRows: 4, patrolInterval: 10,
    sidebarOpen: true, lastLayout: "",
    maxRetries: 3, retryDelay: 10, maxConcurrent: 8,
    // Up to this many tiles per page use main-stream; bigger grids use sub.
    // 4 = ≤ 2×2 grids on main, 3×3 and above on sub.
    mainStreamMaxTiles: 4,
  },
  customOrder: null,
  connections: {},
  stallCheckTimer: null,
  patrol: { active: false, timer: null, countdown: 0, paused: false },
  focusedCell: -1,
  fullscreenPath: null,
  fullscreenConn: null,
  fullscreenToken: 0,
  fullscreenIsMain: true,
  inventory: null,
  streamHealth: {},
  autoDisabledNvrs: new Set(),
  _preconnectTimer: null,
};

export function loadState() {
  try {
    const g = localStorage.getItem(LS.groups);
    if (g) state.groups = JSON.parse(g);
    const l = localStorage.getItem(LS.layouts);
    if (l) state.layouts = JSON.parse(l);
    const p = localStorage.getItem(LS.prefs);
    if (p) Object.assign(state.prefs, JSON.parse(p));
    // Migrate old single-axis gridSize pref → gridCols/gridRows
    if (state.prefs.gridSize && !state.prefs.gridCols) {
      state.prefs.gridCols = state.prefs.gridSize;
      state.prefs.gridRows = state.prefs.gridSize;
      delete state.prefs.gridSize;
    }
    state.gridCols = state.prefs.gridCols || 4;
    state.gridRows = state.prefs.gridRows || 4;
  } catch (_) {}
}

export function saveGroups()  { localStorage.setItem(LS.groups,  JSON.stringify(state.groups)); }
export function saveLayouts() { localStorage.setItem(LS.layouts, JSON.stringify(state.layouts)); }
export function savePrefs()   { localStorage.setItem(LS.prefs,   JSON.stringify(state.prefs)); }
