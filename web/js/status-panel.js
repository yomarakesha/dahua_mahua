/**
 * Status panel: lists active connections grouped by NVR with health counts.
 */

import { state } from "./state.js";
import { dom } from "./dom.js";
import { getNvrId, getChannel } from "./utils.js";
import { connectCamera } from "./streams.js";

const STATUS_ORDER = { error: 0, connecting: 1, live: 2 };

export function renderStatusPanel() {
  dom.statusList.innerHTML = "";

  const byNvr = new Map();
  Object.entries(state.connections).forEach(([path, conn]) => {
    const nvrId = getNvrId(path);
    if (!byNvr.has(nvrId)) byNvr.set(nvrId, []);
    byNvr.get(nvrId).push([path, conn]);
  });

  if (byNvr.size === 0) {
    dom.statusList.innerHTML = "<div style='color:#666;padding:12px'>No active connections</div>";
    return;
  }

  // Sort NVRs by worst status first (errors → connecting → live)
  const sortedNvrs = [...byNvr.entries()].sort((a, b) => {
    const worstA = Math.min(...a[1].map(([, c]) => STATUS_ORDER[c.status] ?? 3));
    const worstB = Math.min(...b[1].map(([, c]) => STATUS_ORDER[c.status] ?? 3));
    return worstA - worstB;
  });

  sortedNvrs.forEach(([nvrId, entries]) => {
    let online = 0, connecting = 0, errored = 0;
    entries.forEach(([, c]) => {
      if (c.status === "live") online++;
      else if (c.status === "connecting") connecting++;
      else if (c.status === "error") errored++;
    });

    const section = document.createElement("div");
    section.className = "status-nvr-section";

    const nvrMeta = state.inventory && state.inventory.nvrs
      ? state.inventory.nvrs.find(n => n.id === nvrId) : null;
    const displayLabel = nvrMeta && nvrMeta.label ? nvrMeta.label : nvrId.toUpperCase();

    const header = document.createElement("div");
    header.className = "status-nvr-header";
    const arrow = document.createElement("span");
    arrow.className = "tree-arrow open";
    arrow.textContent = "▶";
    const title = document.createElement("span");
    title.textContent = displayLabel;
    title.style.cssText = "flex:1;font-weight:600;font-size:13px";
    const counts = document.createElement("span");
    counts.className = "status-nvr-counts";
    counts.innerHTML =
      (online > 0 ? `<span class="ok">${online} live</span>` : "") +
      (connecting > 0 ? `<span class="warn">${connecting} connecting</span>` : "") +
      (errored > 0 ? `<span class="err">${errored} error</span>` : "");
    header.append(arrow, title, counts);

    const body = document.createElement("div");
    body.className = "status-nvr-body open";

    entries.sort((a, b) => (STATUS_ORDER[a[1].status] ?? 3) - (STATUS_ORDER[b[1].status] ?? 3));

    entries.forEach(([path, conn]) => {
      const item = document.createElement("div");
      item.className = "status-item";

      const dot = document.createElement("span");
      dot.className = "status-dot " + conn.status;

      const name = document.createElement("span");
      name.className = "cam-name";
      name.textContent = getChannel(path).toUpperCase();

      const st = document.createElement("span");
      st.textContent = conn.status;
      st.style.cssText = "color:#666;font-size:11px";

      const btn = document.createElement("button");
      btn.textContent = "Reconnect";
      btn.addEventListener("click", () => { if (conn.video) connectCamera(path, conn.video); });

      item.append(dot, name, st, btn);
      body.appendChild(item);
    });

    header.addEventListener("click", () => {
      arrow.classList.toggle("open");
      body.classList.toggle("open");
    });

    section.append(header, body);
    dom.statusList.appendChild(section);
  });
}
