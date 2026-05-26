/**
 * Saved layouts: grid size + filter + camera order, persisted in localStorage.
 */

import { state, saveLayouts, savePrefs } from "./state.js";
import { dom } from "./dom.js";
import { updateGridSizeInput, goToPage } from "./grid.js";
import { applyFilter } from "./sidebar.js";

export function saveLayout(name) {
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

export function loadLayout(name) {
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

export function deleteLayout(name) {
  state.layouts = state.layouts.filter(l => l.name !== name);
  saveLayouts();
  renderLayoutSelect();
}

export function renderLayoutSelect() {
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
    sep.textContent = "────────";
    dom.layoutSel.appendChild(sep);
    state.layouts.forEach(l => {
      const opt = document.createElement("option");
      opt.value = "__delete__" + l.name;
      opt.textContent = "✕ Delete: " + l.name;
      dom.layoutSel.appendChild(opt);
    });
  }
}

export function showLayoutDialog() {
  dom.layoutDialog.classList.remove("hidden");
  dom.layoutNameInput.value = "";
  dom.layoutNameInput.focus();
}

export function hideLayoutDialog() { dom.layoutDialog.classList.add("hidden"); }
