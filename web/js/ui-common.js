/**
 * Shared UI primitives: context menu, modal toggle, sidebar collapse.
 */

import { state, savePrefs } from "./state.js";
import { dom } from "./dom.js";

// ── Context menu ────────────────────────────────────────────────────────────

export function showContextMenu(e, items) {
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

export function hideContextMenu() { dom.contextMenu.classList.add("hidden"); }
document.addEventListener("click", hideContextMenu);

// ── Modals ──────────────────────────────────────────────────────────────────

export function toggleModal(modal) { modal.classList.toggle("hidden"); }

export function initModals() {
  document.querySelectorAll(".modal").forEach(modal => {
    modal.addEventListener("click", (e) => { if (e.target === modal) modal.classList.add("hidden"); });
    modal.querySelectorAll(".modal-close").forEach(btn => {
      btn.addEventListener("click", () => modal.classList.add("hidden"));
    });
  });
}

// ── Sidebar toggle ──────────────────────────────────────────────────────────

export function toggleSidebar() {
  state.prefs.sidebarOpen = !state.prefs.sidebarOpen;
  dom.sidebar.classList.toggle("collapsed", !state.prefs.sidebarOpen);
  savePrefs();
}
