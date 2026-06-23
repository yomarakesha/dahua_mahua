/**
 * Shared mutable state + localStorage persistence.
 */

import { LS } from "./config.js";

export const state = {
  allCameras: [],
  filteredCameras: [],
  gridCols: 4,
  gridRows: 4,
  autoGrid: true,
  currentPage: 0,
  searchText: "",
  activeFilter: { type: "all", value: "" },
  groups: [],
  layouts: [],
  prefs: {
    gridCols: 4, gridRows: 4, patrolInterval: 10,
    // autoGrid: grid size follows the camera count automatically. nvrCap: max
    // distinct streams pulled from a SINGLE NVR per page (the NVR's own RTSP
    // limit is ~13, so 12 keeps a safety margin and guarantees no dead tiles).
    // autoMaxPerPage caps tiles/page so a browser isn't asked to decode too many.
    autoGrid: true, nvrCap: 12, autoMaxPerPage: 16,
    sidebarOpen: true, lastLayout: "",
    maxRetries: 3, retryDelay: 10, maxConcurrent: 8,
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
  cameraByPath: {},
  streamHealth: {},
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
    // Drop the retired mainStreamMaxTiles pref: the grid is always sub-stream
    // now, main is fullscreen-only, so any saved value is dead weight.
    delete state.prefs.mainStreamMaxTiles;
    // Migrate old single-axis gridSize pref → gridCols/gridRows
    if (state.prefs.gridSize && !state.prefs.gridCols) {
      state.prefs.gridCols = state.prefs.gridSize;
      state.prefs.gridRows = state.prefs.gridSize;
      delete state.prefs.gridSize;
    }
    state.gridCols = state.prefs.gridCols || 4;
    state.gridRows = state.prefs.gridRows || 4;
    state.autoGrid = state.prefs.autoGrid !== false; // default ON
  } catch (_) {}
}

export function saveGroups()  { localStorage.setItem(LS.groups,  JSON.stringify(state.groups)); }
export function saveLayouts() { localStorage.setItem(LS.layouts, JSON.stringify(state.layouts)); }
export function savePrefs()   { localStorage.setItem(LS.prefs,   JSON.stringify(state.prefs)); }
