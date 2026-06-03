/**
 * NVR + Camera CRUD against the v2 backend (/api/v1/*).
 *
 * Every action is a live API call — no client-side staging. Each successful
 * create/update/delete triggers a refresh of the affected rows so the UI
 * stays in sync with the DB. MediaMTX paths are reconciled by the backend
 * inside the same request.
 */

import { dom } from "./dom.js";
import { esc } from "./utils.js";
import { dlog } from "./logger.js";
import {
  listNvrs, createNvr, updateNvr, deleteNvr, testNvr, healthAllNvrs,
  setNvrChannels,
  listCameras, createCamera, updateCamera, deleteCamera,
  listRegions, listEvents, reconcileMediamtx,
  changePassword as apiChangePassword, logout as apiLogout,
} from "./api.js";
import { fetchInventory, fetchCameras, reconnectAllVisible } from "./streams.js";

// Cached regions for the "Region" dropdowns. Loaded lazily on first open.
let _regions = [];
// Currently selected NVR for the cameras sub-dialog.
let _currentNvrId = null;

// ── Status helpers ──────────────────────────────────────────────────────────

function setStatus(el, msg, isErr = false) {
  el.textContent = msg || "";
  el.className = !msg ? "" : (isErr ? "err" : "ok");
}

function errMsg(e) {
  return (e && e.message) ? String(e.message) : String(e || "Error");
}

// ── Open / refresh ──────────────────────────────────────────────────────────

export async function openSettings() {
  setStatus(dom.settingsStatus, "");
  setStatus(dom.settingsAddStatus, "");
  dom.settingsModal.classList.remove("hidden");
  try {
    if (_regions.length === 0) _regions = await listRegions();
    populateRegionSelect(dom.settingsNewRegion, "");
    await refreshNvrTable();
  } catch (e) {
    setStatus(dom.settingsStatus, `Load failed: ${errMsg(e)}`, true);
  }
}

function populateRegionSelect(sel, currentId) {
  sel.innerHTML = `<option value="">— None —</option>` +
    _regions.map(r => `<option value="${esc(r.id)}"${r.id === currentId ? " selected" : ""}>${esc(r.name)}</option>`).join("");
}

async function refreshNvrTable() {
  const nvrs = await listNvrs();
  dom.settingsNvrBody.innerHTML = "";
  for (const n of nvrs) dom.settingsNvrBody.appendChild(buildNvrRow(n));
  dom.settingsNvrCount.textContent = String(nvrs.length);
}

// ── NVR row ─────────────────────────────────────────────────────────────────

function buildNvrRow(nvr) {
  const tr = document.createElement("tr");
  if (!nvr.enabled) tr.classList.add("nvr-disabled");
  tr.dataset.nvrId = nvr.id;

  const vendorOpts = ["dahua", "hikvision"].map(v =>
    `<option value="${v}"${nvr.vendor === v ? " selected" : ""}>${v[0].toUpperCase() + v.slice(1)}</option>`
  ).join("");
  const regionOpts = `<option value="">—</option>` +
    _regions.map(r => `<option value="${esc(r.id)}"${r.id === (nvr.region_id || "") ? " selected" : ""}>${esc(r.name)}</option>`).join("");

  tr.innerHTML =
    `<td><input type="checkbox" data-field="enabled" ${nvr.enabled ? "checked" : ""}></td>` +
    `<td class="nvr-id-cell"><code>${esc(nvr.id)}</code><span class="nvr-health-dot hidden"></span></td>` +
    `<td><input type="text" data-field="label" value="${esc(nvr.label)}"></td>` +
    `<td><input type="text" data-field="ip" value="${esc(nvr.ip)}"></td>` +
    `<td><input type="number" data-field="port" min="1" max="65535" value="${nvr.port}"></td>` +
    `<td><input type="text" data-field="rtsp_username" value="${esc(nvr.rtsp_username)}"></td>` +
    `<td><select data-field="vendor">${vendorOpts}</select></td>` +
    `<td class="ch-count">${nvr.camera_count}</td>` +
    `<td><select data-field="region_id">${regionOpts}</select></td>` +
    `<td class="row-actions">` +
      `<button class="row-btn pw" title="Change password">&#128274;</button>` +
      `<button class="row-btn test" title="Test RTSP credentials">Test</button>` +
      `<button class="row-btn cams" title="Manage cameras">Cams</button>` +
      `<button class="row-btn save" title="Save changes" disabled>Save</button>` +
      `<button class="row-btn del" title="Delete NVR">&times;</button>` +
    `</td>`;

  const saveBtn = tr.querySelector(".save");
  const markDirty = () => { saveBtn.disabled = false; };
  tr.querySelectorAll("[data-field]").forEach(el => {
    el.addEventListener("input", markDirty);
    el.addEventListener("change", markDirty);
  });
  tr.querySelector('[data-field="enabled"]').addEventListener("change", (e) => {
    tr.classList.toggle("nvr-disabled", !e.target.checked);
  });

  saveBtn.addEventListener("click", () => saveNvrRow(tr, nvr.id));
  tr.querySelector(".pw").addEventListener("click", () => promptPasswordChange(nvr.id));
  tr.querySelector(".test").addEventListener("click", () => runTest(tr, nvr.id));
  tr.querySelector(".cams").addEventListener("click", () => openCameras(nvr));
  tr.querySelector(".del").addEventListener("click", () => deleteNvrRow(tr, nvr));

  return tr;
}

function readRow(tr) {
  const get = f => (tr.querySelector(`[data-field="${f}"]`) || {}).value || "";
  const chk = f => { const el = tr.querySelector(`[data-field="${f}"]`); return el ? el.checked : false; };
  return {
    enabled: chk("enabled"),
    label: get("label").trim(),
    ip: get("ip").trim(),
    port: parseInt(get("port")) || 554,
    rtsp_username: get("rtsp_username").trim() || "admin",
    vendor: get("vendor") || "dahua",
    region_id: get("region_id") || null,
  };
}

async function saveNvrRow(tr, nvrId) {
  const body = readRow(tr);
  const saveBtn = tr.querySelector(".save");
  saveBtn.disabled = true;
  saveBtn.textContent = "...";
  dlog.info(nvrId, "nvr-update-start", `fields=${Object.keys(body).join(",")} enabled=${body.enabled}`);
  const t0 = performance.now();
  try {
    const updated = await updateNvr(nvrId, body);
    dlog.info(nvrId, "nvr-update-ok",
      `cams=${updated.camera_count} enabled=${updated.enabled} dt=${(performance.now() - t0).toFixed(0)}ms`);
    tr.replaceWith(buildNvrRow(updated));
    setStatus(dom.settingsStatus, `${nvrId} saved`);
    // Push fresh inventory to the dashboard so cards reflect new label/state.
    await fetchInventory();
    await fetchCameras();
  } catch (e) {
    dlog.error(nvrId, "nvr-update-fail", `dt=${(performance.now() - t0).toFixed(0)}ms: ${errMsg(e)}`);
    saveBtn.disabled = false;
    saveBtn.textContent = "Save";
    setStatus(dom.settingsStatus, `${nvrId}: ${errMsg(e)}`, true);
  }
}

async function deleteNvrRow(tr, nvr) {
  if (!confirm(`Delete NVR "${nvr.id}" and all ${nvr.camera_count} cameras? This cannot be undone.`)) return;
  dlog.warn(nvr.id, "nvr-delete-start", `cams=${nvr.camera_count}`);
  try {
    await deleteNvr(nvr.id);
    dlog.info(nvr.id, "nvr-delete-ok");
    tr.remove();
    dom.settingsNvrCount.textContent = String(dom.settingsNvrBody.children.length);
    setStatus(dom.settingsStatus, `${nvr.id} deleted`);
    await fetchInventory();
    await fetchCameras();
  } catch (e) {
    dlog.error(nvr.id, "nvr-delete-fail", errMsg(e));
    setStatus(dom.settingsStatus, `${nvr.id}: ${errMsg(e)}`, true);
  }
}

async function runTest(tr, nvrId) {
  const btn = tr.querySelector(".test");
  btn.disabled = true;
  btn.textContent = "...";
  dlog.info(nvrId, "nvr-test-start");
  const t0 = performance.now();
  try {
    const res = await testNvr(nvrId);
    dlog.info(nvrId, "nvr-test-done",
      `ok=${res.ok} dt=${(performance.now() - t0).toFixed(0)}ms msg="${String(res.message).slice(0, 200)}"`);
    btn.textContent = res.ok ? "OK" : "Fail";
    btn.style.color = res.ok ? "#4caf50" : "#f44336";
    setStatus(dom.settingsStatus, `${nvrId}: ${res.message}`, !res.ok);
  } catch (e) {
    dlog.error(nvrId, "nvr-test-fail", `dt=${(performance.now() - t0).toFixed(0)}ms: ${errMsg(e)}`);
    btn.textContent = "Err";
    btn.style.color = "#f44336";
    setStatus(dom.settingsStatus, `${nvrId}: ${errMsg(e)}`, true);
  }
  setTimeout(() => {
    btn.disabled = false;
    btn.textContent = "Test";
    btn.style.color = "";
  }, 5000);
}

async function promptPasswordChange(nvrId) {
  const pw = prompt(`Set new RTSP password for ${nvrId}:`);
  if (pw == null || pw === "") return;
  try {
    await updateNvr(nvrId, { rtsp_password: pw });
    setStatus(dom.settingsStatus, `${nvrId}: password updated`);
  } catch (e) {
    setStatus(dom.settingsStatus, `${nvrId}: ${errMsg(e)}`, true);
  }
}

// ── Add NVR ─────────────────────────────────────────────────────────────────

export async function addNvr() {
  const id = dom.settingsNewId.value.trim();
  const ip = dom.settingsNewIp.value.trim();
  const pw = dom.settingsNewPass.value;
  const label = dom.settingsNewLabel.value.trim();
  // 0 / blank → null → backend auto-detects.
  const chRaw = parseInt(dom.settingsNewCh.value);
  const channels = (Number.isFinite(chRaw) && chRaw > 0) ? chRaw : null;

  if (!ip || !/^(\d{1,3}\.){3}\d{1,3}$/.test(ip)) {
    setStatus(dom.settingsAddStatus, "Invalid IP", true);
    return;
  }
  if (!pw) {
    setStatus(dom.settingsAddStatus, "Password is required", true);
    return;
  }
  if (!label) {
    setStatus(dom.settingsAddStatus, "Name is required", true);
    return;
  }
  if (id && !/^[a-z0-9][a-z0-9_-]*$/.test(id)) {
    setStatus(dom.settingsAddStatus, "ID must be lowercase alnum / dash / underscore", true);
    return;
  }

  const body = {
    label,
    ip,
    port: parseInt(dom.settingsNewPort.value) || 554,
    rtsp_username: dom.settingsNewUser.value.trim() || "admin",
    rtsp_password: pw,
    vendor: dom.settingsNewVendor.value || "dahua",
    channels,
    enabled: dom.settingsNewEnabled.checked,
    region_id: dom.settingsNewRegion.value || null,
  };
  if (id) body.id = id;

  dom.settingsAddBtn.disabled = true;
  setStatus(
    dom.settingsAddStatus,
    channels == null
      ? "Adding... (probing credentials + auto-detecting channels, ~5s)"
      : "Adding... (probing credentials, ~3s)",
  );
  dlog.info(ip, "nvr-add-start",
    `label="${label}" vendor=${body.vendor} channels=${channels ?? "auto"} enabled=${body.enabled}`);
  const t0 = performance.now();
  try {
    // Backend pre-probes RTSP creds BEFORE writing. Wrong password → 400,
    // nothing saved → we just surface the error. Banned IP → 409 with
    // cooldown. So we can drop the old "create-then-test-then-disable"
    // dance entirely.
    const created = await createNvr(body);
    const dt = (performance.now() - t0).toFixed(0);
    dlog.info(ip, "nvr-add-ok",
      `id=${created.id} cams=${created.camera_count} enabled=${created.enabled} dt=${dt}ms` +
      (created.create_notice ? ` notice="${String(created.create_notice).slice(0, 200)}"` : ""));
    const msg = created.create_notice
      ? `✓ Added ${created.id} (${created.camera_count} ch). ${created.create_notice}`
      : `✓ Added ${created.id} (${created.camera_count} channels)`;
    setStatus(dom.settingsAddStatus, msg, !created.enabled);
    // Clear form on success.
    dom.settingsNewId.value = "";
    dom.settingsNewLabel.value = "";
    dom.settingsNewIp.value = "";
    dom.settingsNewPass.value = "";
    dom.settingsNewCh.value = "";
    await refreshNvrTable();
    await fetchInventory();
    await fetchCameras();
  } catch (e) {
    // Backend errors carry concrete messages from probe_rtsp / lockouts —
    // surface them as-is; that's already the most informative thing we have.
    dlog.error(ip, "nvr-add-fail", `dt=${(performance.now() - t0).toFixed(0)}ms: ${errMsg(e)}`);
    setStatus(dom.settingsAddStatus, errMsg(e), true);
  } finally {
    dom.settingsAddBtn.disabled = false;
  }
}

// ── Bulk actions ────────────────────────────────────────────────────────────

export async function runHealth() {
  dom.settingsHealthBtn.disabled = true;
  dom.settingsHealthBtn.textContent = "...";
  setStatus(dom.settingsStatus, "");
  try {
    const results = await healthAllNvrs();
    const byId = new Map(results.map(r => [r.nvr_id, r]));
    let ok = 0, fail = 0, disabled = 0;
    dom.settingsNvrBody.querySelectorAll("tr").forEach(tr => {
      const id = tr.dataset.nvrId;
      const r = byId.get(id);
      const dot = tr.querySelector(".nvr-health-dot");
      if (!r || !dot) return;
      dot.classList.remove("hidden", "ok", "fail", "disabled");
      if (r.message === "Disabled") {
        dot.classList.add("disabled"); dot.title = "Disabled"; disabled++;
      } else if (r.ok) {
        dot.classList.add("ok"); dot.title = "Reachable"; ok++;
      } else {
        dot.classList.add("fail"); dot.title = r.message; fail++;
      }
    });
    setStatus(dom.settingsStatus, `${ok} reachable, ${fail} unreachable, ${disabled} disabled`, fail > 0);
  } catch (e) {
    setStatus(dom.settingsStatus, `Health failed: ${errMsg(e)}`, true);
  } finally {
    dom.settingsHealthBtn.disabled = false;
    dom.settingsHealthBtn.textContent = "Health";
  }
}

export async function runReconcile() {
  dom.settingsReconcileBtn.disabled = true;
  setStatus(dom.settingsStatus, "Reconciling MediaMTX...");
  try {
    const r = await reconcileMediamtx();
    const summary = r.summary || `added=${r.added?.length || 0} patched=${r.patched?.length || 0} deleted=${r.deleted?.length || 0} errors=${r.errors?.length || 0}`;
    setStatus(dom.settingsStatus, `Reconcile: ${summary}`, (r.errors || []).length > 0);
    reconnectAllVisible();
  } catch (e) {
    setStatus(dom.settingsStatus, `Reconcile failed: ${errMsg(e)}`, true);
  } finally {
    dom.settingsReconcileBtn.disabled = false;
  }
}

export async function refreshNvrs() {
  setStatus(dom.settingsStatus, "Refreshing...");
  try {
    await refreshNvrTable();
    setStatus(dom.settingsStatus, "");
  } catch (e) {
    setStatus(dom.settingsStatus, errMsg(e), true);
  }
}

// ── Cameras sub-dialog ──────────────────────────────────────────────────────

async function openCameras(nvr) {
  _currentNvrId = nvr.id;
  dom.camerasDialogNvr.textContent = `${nvr.id} (${esc(nvr.label)})`;
  dom.camerasDialog.classList.remove("hidden");
  setStatus(dom.camerasStatus, "");
  setStatus(dom.camerasAddStatus, "");
  dom.camerasNewName.value = "";
  await refreshCameras();
}

async function refreshCameras() {
  if (!_currentNvrId) return;
  const all = await listCameras({ include_disabled: true });
  const cams = all.filter(c => c.nvr_id === _currentNvrId).sort((a, b) => a.channel - b.channel);
  dom.camerasBody.innerHTML = "";

  // Next channel suggestion = max + 1.
  const maxCh = cams.reduce((m, c) => Math.max(m, c.channel), 0);
  dom.camerasNewCh.value = maxCh + 1;

  for (const c of cams) renderCameraRow(c);
}

function renderCameraRow(cam) {
  const tr = document.createElement("tr");
  if (!cam.enabled) tr.classList.add("nvr-disabled");
  tr.dataset.cameraId = cam.id;
  tr.innerHTML =
    `<td><input type="checkbox" data-field="enabled" ${cam.enabled ? "checked" : ""}></td>` +
    `<td class="ch-count">${cam.channel}</td>` +
    `<td><input type="text" data-field="name" value="${esc(cam.name || "")}" placeholder="${esc(cam.display_name)}"></td>` +
    `<td><input type="checkbox" data-field="has_sub" ${cam.has_sub ? "checked" : ""}></td>` +
    `<td><input type="checkbox" data-field="has_main" ${cam.has_main ? "checked" : ""}></td>` +
    `<td class="row-actions">` +
      `<button class="row-btn save" disabled>Save</button>` +
      `<button class="row-btn del" title="Delete channel">&times;</button>` +
    `</td>`;

  const saveBtn = tr.querySelector(".save");
  const markDirty = () => { saveBtn.disabled = false; };
  tr.querySelectorAll("[data-field]").forEach(el => {
    el.addEventListener("input", markDirty);
    el.addEventListener("change", markDirty);
  });
  tr.querySelector('[data-field="enabled"]').addEventListener("change", (e) => {
    tr.classList.toggle("nvr-disabled", !e.target.checked);
  });

  saveBtn.addEventListener("click", async () => {
    const body = {
      enabled: tr.querySelector('[data-field="enabled"]').checked,
      name: tr.querySelector('[data-field="name"]').value.trim() || null,
      has_sub: tr.querySelector('[data-field="has_sub"]').checked,
      has_main: tr.querySelector('[data-field="has_main"]').checked,
    };
    saveBtn.disabled = true;
    saveBtn.textContent = "...";
    try {
      await updateCamera(cam.id, body);
      saveBtn.textContent = "Save";
      setStatus(dom.camerasStatus, `ch${cam.channel} saved`);
      await fetchCameras();
    } catch (e) {
      saveBtn.textContent = "Save";
      saveBtn.disabled = false;
      setStatus(dom.camerasStatus, errMsg(e), true);
    }
  });

  tr.querySelector(".del").addEventListener("click", async () => {
    if (!confirm(`Delete channel ${cam.channel} from ${_currentNvrId}?`)) return;
    try {
      await deleteCamera(cam.id);
      tr.remove();
      setStatus(dom.camerasStatus, `ch${cam.channel} deleted`);
      // Update parent NVR row's CH count.
      const parent = dom.settingsNvrBody.querySelector(`tr[data-nvr-id="${CSS.escape(_currentNvrId)}"] .ch-count`);
      if (parent) parent.textContent = String(parseInt(parent.textContent) - 1);
      await fetchCameras();
    } catch (e) {
      setStatus(dom.camerasStatus, errMsg(e), true);
    }
  });

  dom.camerasBody.appendChild(tr);
}

export async function setChannels() {
  if (!_currentNvrId) return;
  const count = parseInt(dom.camerasSetCount.value);
  if (!count || count < 1 || count > 512) {
    setStatus(dom.camerasSetStatus, "Enter a channel count 1..512", true);
    return;
  }
  const prune = dom.camerasSetPrune.checked;
  if (prune && !confirm(`Set ${_currentNvrId} to exactly ${count} channels?\nChannels above ${count} will be DELETED.`)) return;

  dom.camerasSetBtn.disabled = true;
  setStatus(dom.camerasSetStatus, "Applying...");
  dlog.info(_currentNvrId, "set-channels-start", `count=${count} prune=${prune}`);
  try {
    const updated = await setNvrChannels(_currentNvrId, count, prune);
    dlog.info(_currentNvrId, "set-channels-ok", `cams=${updated.camera_count} notice="${updated.create_notice || ""}"`);
    setStatus(dom.camerasSetStatus, updated.create_notice || `Now ${updated.camera_count} channels`);
    await refreshCameras();
    // Reflect new CH count on the parent NVR row.
    const parent = dom.settingsNvrBody.querySelector(`tr[data-nvr-id="${CSS.escape(_currentNvrId)}"] .ch-count`);
    if (parent) parent.textContent = String(updated.camera_count);
    await fetchCameras();
  } catch (e) {
    dlog.error(_currentNvrId, "set-channels-fail", errMsg(e));
    setStatus(dom.camerasSetStatus, errMsg(e), true);
  } finally {
    dom.camerasSetBtn.disabled = false;
  }
}

export async function addCamera() {
  if (!_currentNvrId) return;
  const channel = parseInt(dom.camerasNewCh.value);
  if (!channel || channel < 1 || channel > 512) {
    setStatus(dom.camerasAddStatus, "Channel must be 1..512", true);
    return;
  }
  const body = {
    nvr_id: _currentNvrId,
    channel,
    name: dom.camerasNewName.value.trim() || null,
    enabled: dom.camerasNewEnabled.checked,
    has_sub: dom.camerasNewSub.checked,
    has_main: dom.camerasNewMain.checked,
  };
  dom.camerasAddBtn.disabled = true;
  setStatus(dom.camerasAddStatus, "Adding...");
  try {
    await createCamera(body);
    setStatus(dom.camerasAddStatus, `Added ch${channel}`);
    dom.camerasNewName.value = "";
    await refreshCameras();
    const parent = dom.settingsNvrBody.querySelector(`tr[data-nvr-id="${CSS.escape(_currentNvrId)}"] .ch-count`);
    if (parent) parent.textContent = String(parseInt(parent.textContent) + 1);
    await fetchCameras();
  } catch (e) {
    setStatus(dom.camerasAddStatus, errMsg(e), true);
  } finally {
    dom.camerasAddBtn.disabled = false;
  }
}

// ── Events log ──────────────────────────────────────────────────────────────

export async function openEventsLog() {
  dom.eventsDialog.classList.remove("hidden");
  dom.eventsList.innerHTML = "<div style='color:#888'>Loading...</div>";
  try {
    const events = await listEvents({ limit: 200 });
    if (!events || events.length === 0) {
      dom.eventsList.innerHTML = "<div style='color:#888'>No events recorded yet</div>";
      return;
    }
    dom.eventsList.innerHTML = events.map(e => {
      const time = new Date(e.created_at).toLocaleString();
      return `<div class="event-item">` +
        `<span class="event-time">${esc(time)}</span>` +
        `<span class="event-nvr">${esc(e.nvr_id)}</span>` +
        `<span class="event-type ${esc(e.event_type)}">${esc(e.event_type)}</span>` +
        `<span>${esc(e.message || "")}</span>` +
      `</div>`;
    }).join("");
  } catch (e) {
    dom.eventsList.innerHTML = `<div style='color:#f44336'>Failed to load events: ${esc(errMsg(e))}</div>`;
  }
}

// ── Password & auth ─────────────────────────────────────────────────────────

export async function changePasswordHandler() {
  const cur = dom.settingsCurPw.value;
  const newPw = dom.settingsNewPw.value;
  if (!cur || !newPw) {
    setStatus(dom.settingsChpwStatus, "Both fields required", true);
    return;
  }
  if (newPw.length < 8) {
    setStatus(dom.settingsChpwStatus, "Min 8 characters", true);
    return;
  }
  try {
    await apiChangePassword(cur, newPw);
    setStatus(dom.settingsChpwStatus, "Password changed");
    dom.settingsCurPw.value = "";
    dom.settingsNewPw.value = "";
  } catch (e) {
    setStatus(dom.settingsChpwStatus, errMsg(e), true);
  }
}

export async function logout() {
  await apiLogout();
}
