/**
 * Sidebar tree (NVRs + Groups), filtering, group dialog, NVR rename.
 */

import { state, saveGroups } from "./state.js";
import { dom } from "./dom.js";
import {
  getNvrId, getChannel, formatName, labelFor, getNvrList,
  gridCells, totalPages, scheduleStatusUpdate,
} from "./utils.js";
import { showContextMenu } from "./ui-common.js";

let _onAutoFit = () => {};
let _renderGridFn = () => {};

export function bindSidebarHooks({ autoFitGrid, renderGrid }) {
  _onAutoFit = autoFitGrid;
  _renderGridFn = renderGrid;
}

// ── Filtering ───────────────────────────────────────────────────────────────

export function applyFilter(type, value) {
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
    cams = cams.filter(p =>
      p.toLowerCase().includes(q) ||
      formatName(p).toLowerCase().includes(q) ||
      labelFor(p).toLowerCase().includes(q)
    );
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

  if (type === "nvr" || type === "group") {
    _onAutoFit(cams.length);
  } else {
    const tp = totalPages();
    if (state.currentPage >= tp) state.currentPage = tp - 1;
    if (state.currentPage < 0) state.currentPage = 0;
    _renderGridFn();
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

// ── Sidebar tree ────────────────────────────────────────────────────────────

export function renderSidebar() {
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
    arrow.textContent = "▶";

    const nvrMeta = state.inventory && state.inventory.nvrs
      ? state.inventory.nvrs.find(n => n.id === nvrId) : null;
    const isDisabled = nvrMeta && nvrMeta.enabled === false;
    const displayLabel = nvrMeta && nvrMeta.label ? nvrMeta.label : nvrId.toUpperCase();
    const vendor = nvrMeta && nvrMeta.vendor ? nvrMeta.vendor.toLowerCase() : "dahua";

    if (isDisabled) header.classList.add("tree-nvr-disabled");

    const label = document.createElement("span");
    label.textContent = `${displayLabel} (${cameras.length})${isDisabled ? " - off" : ""}`;
    label.style.cssText = "overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1";

    const vendorBadge = document.createElement("span");
    vendorBadge.className = `tree-nvr-vendor ${vendor}`;
    vendorBadge.textContent = vendor === "hikvision" ? "HIK" : vendor === "generic" ? "GEN" : "DAH";
    vendorBadge.title = `Vendor: ${vendor}`;

    header.appendChild(arrow);
    header.appendChild(label);
    header.appendChild(vendorBadge);

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
    arrow.textContent = "▶";

    const label = document.createElement("span");
    label.textContent = `${grp.name} (${grp.cameras.length})`;
    label.style.cssText = "flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap";

    const editBtn = document.createElement("span");
    editBtn.className = "tree-edit-btn";
    editBtn.textContent = "✎";
    editBtn.title = "Edit group";
    editBtn.addEventListener("click", (e) => { e.stopPropagation(); showGroupDialog(grp.name); });

    header.appendChild(arrow);
    header.appendChild(label);
    header.appendChild(editBtn);

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
        { label: "Edit group", action: () => showGroupDialog(grp.name) },
        { type: "separator" },
        { label: "Delete group", action: () => { if (confirm("Delete group \"" + grp.name + "\"?")) deleteGroup(grp.name); } },
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
    if (state._goToPage) state._goToPage(Math.floor(idx2 / gridCells()));
    return;
  }
  if (state._goToPage) state._goToPage(Math.floor(idx / gridCells()));
  if (state._setFocusedCell) state._setFocusedCell(idx % gridCells());
}

// ── Groups ──────────────────────────────────────────────────────────────────

let _editingGroup = null;

export function createGroup(name, cameras) {
  if (!name) return;
  const existing = state.groups.find(g => g.name === name);
  if (existing) {
    existing.cameras = cameras || [];
  } else {
    state.groups.push({ name, cameras: cameras || [] });
  }
  saveGroups();
  renderGroupTree();
}

export function addToGroup(groupName, cameraPath) {
  const grp = state.groups.find(g => g.name === groupName);
  if (!grp || grp.cameras.includes(cameraPath)) return;
  grp.cameras.push(cameraPath);
  saveGroups();
  renderGroupTree();
}

export function removeFromGroup(groupName, cameraPath) {
  const grp = state.groups.find(g => g.name === groupName);
  if (!grp) return;
  grp.cameras = grp.cameras.filter(c => c !== cameraPath);
  saveGroups();
  renderGroupTree();
  if (state.activeFilter.type === "group" && state.activeFilter.value === groupName) applyFilter();
}

export function deleteGroup(name) {
  state.groups = state.groups.filter(g => g.name !== name);
  saveGroups();
  renderGroupTree();
  if (state.activeFilter.type === "group" && state.activeFilter.value === name) applyFilter("all", "");
}

export function showGroupDialog(editGroupName) {
  _editingGroup = editGroupName || null;
  const grp = _editingGroup ? state.groups.find(g => g.name === _editingGroup) : null;

  dom.groupDialogTitle.textContent = grp ? "Edit Group" : "New Group";
  dom.groupNameInput.value = grp ? grp.name : "";
  dom.groupCamSearch.value = "";
  dom.groupCreateBtn.textContent = grp ? "Save" : "Create";

  renderGroupCameraPicker(grp ? new Set(grp.cameras) : new Set());
  dom.groupDialog.classList.remove("hidden");
  dom.groupNameInput.focus();
}

export function hideGroupDialog() {
  dom.groupDialog.classList.add("hidden");
  _editingGroup = null;
}

function renderGroupCameraPicker(selectedSet) {
  const nvrs = getNvrList();
  dom.groupCamList.innerHTML = "";

  [...nvrs.keys()].sort().forEach(nvrId => {
    const cameras = nvrs.get(nvrId);
    if (cameras.length === 0) return;

    const section = document.createElement("div");
    section.className = "group-nvr-section";

    const nvrMeta = state.inventory && state.inventory.nvrs
      ? state.inventory.nvrs.find(n => n.id === nvrId) : null;
    const displayLabel = nvrMeta && nvrMeta.label ? nvrMeta.label : nvrId.toUpperCase();

    const header = document.createElement("div");
    header.className = "group-nvr-header";

    const toggle = document.createElement("span");
    toggle.className = "nvr-toggle open";
    toggle.textContent = "▶";

    const headerLabel = document.createElement("span");
    const selectedInNvr = cameras.filter(c => selectedSet.has(c)).length;
    headerLabel.textContent = `${displayLabel} (${selectedInNvr}/${cameras.length})`;

    header.appendChild(toggle);
    header.appendChild(headerLabel);

    const camsDiv = document.createElement("div");
    camsDiv.className = "group-nvr-cams open";

    cameras.forEach(cam => {
      const item = document.createElement("div");
      item.className = "group-cam-item";
      item.dataset.cam = cam;

      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = selectedSet.has(cam);
      cb.id = "gcb_" + cam;

      const lbl = document.createElement("label");
      lbl.htmlFor = cb.id;
      lbl.textContent = labelFor(cam);

      cb.addEventListener("change", () => {
        if (cb.checked) selectedSet.add(cam);
        else selectedSet.delete(cam);
        updateGroupPickerCounts(selectedSet);
      });

      item.addEventListener("click", (e) => {
        if (e.target !== cb) { cb.checked = !cb.checked; cb.dispatchEvent(new Event("change")); }
      });

      item.appendChild(cb);
      item.appendChild(lbl);
      camsDiv.appendChild(item);
    });

    header.addEventListener("click", () => {
      toggle.classList.toggle("open");
      camsDiv.classList.toggle("open");
    });

    section.appendChild(header);
    section.appendChild(camsDiv);
    dom.groupCamList.appendChild(section);
  });

  updateGroupPickerCounts(selectedSet);
}

function updateGroupPickerCounts(selectedSet) {
  dom.groupSelectedCount.textContent = selectedSet.size + " selected";

  dom.groupCamList.querySelectorAll(".group-nvr-section").forEach(section => {
    const cbs = section.querySelectorAll('input[type="checkbox"]');
    const checked = [...cbs].filter(cb => cb.checked).length;
    const headerLabel = section.querySelector(".group-nvr-header span:last-child");
    if (headerLabel) {
      const base = headerLabel.textContent.replace(/\(\d+\/\d+\)/, "").trim();
      headerLabel.textContent = `${base} (${checked}/${cbs.length})`;
    }
  });
}

function getGroupDialogSelected() {
  const selected = [];
  dom.groupCamList.querySelectorAll('input[type="checkbox"]:checked').forEach(cb => {
    const item = cb.closest(".group-cam-item");
    if (item && item.dataset.cam) selected.push(item.dataset.cam);
  });
  return selected;
}

export function filterGroupCameras(query) {
  const q = query.toLowerCase();
  dom.groupCamList.querySelectorAll(".group-cam-item").forEach(item => {
    const cam = item.dataset.cam || "";
    const match = !q || cam.toLowerCase().includes(q) || formatName(cam).toLowerCase().includes(q) || labelFor(cam).toLowerCase().includes(q);
    item.style.display = match ? "" : "none";
  });
  dom.groupCamList.querySelectorAll(".group-nvr-section").forEach(section => {
    const visibleCams = section.querySelectorAll('.group-cam-item:not([style*="display: none"])');
    section.style.display = visibleCams.length > 0 ? "" : "none";
  });
}

export function saveGroupDialog() {
  const name = dom.groupNameInput.value.trim();
  if (!name) return;

  const cameras = getGroupDialogSelected();

  if (_editingGroup && _editingGroup !== name) {
    const oldFilter = state.activeFilter.type === "group" && state.activeFilter.value === _editingGroup;
    state.groups = state.groups.filter(g => g.name !== _editingGroup);
    createGroup(name, cameras);
    if (oldFilter) applyFilter("group", name);
  } else {
    createGroup(name, cameras);
  }

  hideGroupDialog();
}

export function toggleGroupSelectAll() {
  const visible = dom.groupCamList.querySelectorAll('.group-cam-item:not([style*="display: none"]) input[type="checkbox"]');
  const allChecked = [...visible].every(cb => cb.checked);
  visible.forEach(cb => { cb.checked = !allChecked; cb.dispatchEvent(new Event("change")); });
}

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
