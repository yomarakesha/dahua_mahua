/**
 * Settings modal: NVR table CRUD, import CSV/JSON, health/test buttons,
 * events log, lockout management, change-password, restart MediaMTX.
 */

import { state, savePrefs } from "./state.js";
import { dom } from "./dom.js";
import { esc } from "./utils.js";
import { fetchInventory, fetchCameras, reconnectAllVisible } from "./streams.js";
import { renderSidebar } from "./sidebar.js";

// ── Open / render ───────────────────────────────────────────────────────────

export function openSettings() {
  setSettingsStatus("");
  fetch("/api/inventory")
    .then(r => {
      if (r.status === 401) { location.href = "/login"; return; }
      return r.json();
    })
    .then(inv => {
      if (!inv) return;
      renderSettingsForm(inv);
      dom.settingsModal.classList.remove("hidden");
    })
    .catch(e => setSettingsStatus("Failed to load inventory: " + e, true));
}

function renderSettingsForm(inv) {
  const g = inv.global || {};
  dom.settingsPort.value = g.default_port || 554;
  dom.settingsUser.value = g.default_username || "";
  dom.settingsPass.value = g.default_password || "";
  dom.settingsSubtype.value = g.default_subtype != null ? g.default_subtype : 1;
  dom.settingsStreamSource.value = g.stream_source || "nvr";
  dom.settingsServerUrl.value = g.server_url || "";

  dom.settingsMaxRetries.value = state.prefs.maxRetries;
  dom.settingsRetryDelay.value = state.prefs.retryDelay;
  dom.settingsMaxConcurrent.value = state.prefs.maxConcurrent;

  dom.settingsNvrBody.innerHTML = "";
  (inv.nvrs || []).forEach(nvr => appendNvrRow(nvr));
  updateNvrCount();
  clearAddRow();
  dom.settingsHealthStatus.textContent = "";
}

// ── NVR row ─────────────────────────────────────────────────────────────────

function appendNvrRow(nvr) {
  const enabled = nvr.enabled !== false;
  const srcVal = nvr.stream_source || "";
  const vendorVal = (nvr.vendor || "dahua").toLowerCase();
  const tr = document.createElement("tr");
  if (!enabled) tr.classList.add("nvr-disabled");
  tr.dataset.nvrId = nvr.id || "";
  const vendorOpts = ["dahua", "hikvision", "generic"].map(v =>
    `<option value="${v}"${vendorVal === v ? " selected" : ""}>${v[0].toUpperCase() + v.slice(1)}</option>`
  ).join("");
  tr.innerHTML =
    `<td><input type="checkbox" data-field="enabled" ${enabled ? "checked" : ""} title="Enable/disable this NVR"></td>` +
    `<td><input type="text" value="${esc(nvr.id)}" data-field="id"><span class="nvr-health-dot hidden"></span></td>` +
    `<td><input type="text" value="${esc(nvr.label || "")}" data-field="label"></td>` +
    `<td><input type="text" value="${esc(nvr.ip)}" data-field="ip"></td>` +
    `<td><input type="number" min="1" value="${nvr.channels || 1}" data-field="channels"></td>` +
    `<td><span class="pw-field"><input type="password" value="${esc(nvr.password || "")}" placeholder="(global)" data-field="password"><button type="button" class="pw-toggle" title="Show/hide">&#128065;</button></span></td>` +
    `<td><select data-field="vendor">${vendorOpts}</select></td>` +
    `<td><select data-field="stream_source"><option value="">Default</option><option value="nvr"${srcVal === "nvr" ? " selected" : ""}>NVR</option><option value="server"${srcVal === "server" ? " selected" : ""}>Server</option></select></td>` +
    `<td><div class="nvr-actions">` +
      `<button class="settings-row-btn test" title="Test RTSP credentials">Test</button>` +
      `<button class="settings-row-btn del" title="Remove">&times;</button>` +
    `</div></td>`;

  tr.querySelector('[data-field="enabled"]').addEventListener("change", (e) => {
    tr.classList.toggle("nvr-disabled", !e.target.checked);
  });

  const pwToggle = tr.querySelector(".pw-toggle");
  if (pwToggle) {
    pwToggle.addEventListener("click", (e) => {
      e.preventDefault();
      const inp = tr.querySelector('[data-field="password"]');
      inp.type = inp.type === "password" ? "text" : "password";
    });
  }

  tr.querySelector(".test").addEventListener("click", async () => {
    const ip = tr.querySelector('[data-field="ip"]').value.trim();
    const pw = tr.querySelector('[data-field="password"]').value || dom.settingsPass.value;
    const user = dom.settingsUser.value.trim() || "admin";
    const port = parseInt(dom.settingsPort.value) || 554;
    const nvrId = tr.querySelector('[data-field="id"]').value.trim();
    const vendor = tr.querySelector('[data-field="vendor"]').value || "dahua";
    const btn = tr.querySelector(".test");
    btn.disabled = true;
    btn.textContent = "...";
    setSettingsStatus("");
    try {
      const res = await fetch("/api/test-nvr", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ip, port, username: user, password: pw, nvr_id: nvrId, vendor }),
      });
      const data = await res.json();
      if (data.ok) {
        btn.textContent = "OK";
        btn.style.color = "#4caf50";
        btn.style.borderColor = "#4caf50";
        setSettingsStatus(`${ip}: Connection OK`, false);
        clearBanTimer(tr);
      } else {
        btn.textContent = "Fail";
        btn.style.color = "#f44336";
        btn.style.borderColor = "#f44336";
        setSettingsStatus(`${ip}: ${data.message}`, true);
        if (data.banned_until) showBanTimer(tr, data.banned_until);
      }
    } catch (e) {
      btn.textContent = "Err";
      btn.style.color = "#f44336";
      btn.style.borderColor = "#f44336";
      setSettingsStatus("Network error testing NVR", true);
    }
    setTimeout(() => {
      btn.disabled = false;
      btn.textContent = "Test";
      btn.style.color = "";
      btn.style.borderColor = "";
    }, 6000);
  });

  tr.querySelector(".del").addEventListener("click", () => { tr.remove(); updateNvrCount(); });

  dom.settingsNvrBody.appendChild(tr);
}

function showBanTimer(tr, bannedUntil) {
  clearBanTimer(tr);
  const span = document.createElement("span");
  span.className = "nvr-ban-timer";
  const idCell = tr.querySelector('[data-field="id"]');
  if (idCell) idCell.parentElement.appendChild(span);
  const tick = () => {
    const rem = Math.max(0, Math.ceil(bannedUntil - Date.now() / 1000));
    if (rem <= 0) {
      clearBanTimer(tr);
      return;
    }
    const m = Math.floor(rem / 60);
    const s = rem % 60;
    span.textContent = `🚫 ${m}m ${s}s`;
    span._timer = setTimeout(tick, 1000);
  };
  tick();
}

function clearBanTimer(tr) {
  const existing = tr.querySelector(".nvr-ban-timer");
  if (existing) {
    if (existing._timer) clearTimeout(existing._timer);
    existing.remove();
  }
}

function updateNvrCount() {
  dom.settingsNvrCount.textContent = dom.settingsNvrBody.querySelectorAll("tr").length;
}

function clearAddRow() {
  dom.settingsNewId.value = nextNvrId();
  dom.settingsNewLabel.value = "";
  dom.settingsNewIp.value = "";
  dom.settingsNewCh.value = 1;
  dom.settingsNewPass.value = "";
  dom.settingsNewVendor.value = "dahua";
}

function nextNvrId() {
  const rows = dom.settingsNvrBody.querySelectorAll("tr");
  let max = 0;
  rows.forEach(r => {
    const id = r.querySelector('[data-field="id"]').value;
    const m = id.match(/nvr(\d+)/);
    if (m) max = Math.max(max, parseInt(m[1]));
  });
  return "nvr" + String(max + 1).padStart(2, "0");
}

export function addNvrFromFooter() {
  const id = dom.settingsNewId.value.trim();
  const ip = dom.settingsNewIp.value.trim();
  const ch = parseInt(dom.settingsNewCh.value) || 1;
  if (!id || !ip) { setSettingsStatus("ID and IP are required", true); return; }
  if (!/^(\d{1,3}\.){3}\d{1,3}$/.test(ip)) { setSettingsStatus("Invalid IP format", true); return; }
  if (ch < 1 || ch > 256) { setSettingsStatus("Channels must be 1-256", true); return; }
  const vendor = dom.settingsNewVendor.value || "dahua";
  const defaultLabel = `${vendor[0].toUpperCase() + vendor.slice(1)} ${ip}`;
  appendNvrRow({
    id,
    label: dom.settingsNewLabel.value.trim() || defaultLabel,
    ip,
    channels: ch,
    password: dom.settingsNewPass.value,
    vendor,
    enabled: true,
  });
  updateNvrCount();
  clearAddRow();
  dom.settingsNewIp.focus();
}

function harvestInventory() {
  const inv = {
    global: {
      default_port: parseInt(dom.settingsPort.value) || 554,
      default_username: dom.settingsUser.value.trim() || "admin",
      default_password: dom.settingsPass.value,
      default_subtype: parseInt(dom.settingsSubtype.value),
      stream_source: dom.settingsStreamSource.value || "nvr",
      server_url: dom.settingsServerUrl.value.trim(),
    },
    nvrs: [],
  };
  dom.settingsNvrBody.querySelectorAll("tr").forEach(tr => {
    const get = f => (tr.querySelector(`[data-field="${f}"]`) || {}).value || "";
    const chk = f => { const el = tr.querySelector(`[data-field="${f}"]`); return el ? el.checked : true; };
    const vendor = get("vendor") || "dahua";
    const nvr = {
      id: get("id").trim(),
      label: get("label").trim(),
      ip: get("ip").trim(),
      channels: parseInt(get("channels")) || 1,
      vendor,
      enabled: chk("enabled"),
    };
    const pw = get("password");
    if (pw) nvr.password = pw;
    const src = get("stream_source");
    if (src) nvr.stream_source = src;
    inv.nvrs.push(nvr);
  });
  return inv;
}

export function setSettingsStatus(msg, isErr) {
  dom.settingsStatus.textContent = msg;
  dom.settingsStatus.className = isErr ? "err" : msg ? "ok" : "";
}

function validateInventory(inv) {
  const ipRe = /^(\d{1,3}\.){3}\d{1,3}$/;
  const port = inv.global.default_port;
  if (port < 1 || port > 65535) return "Port must be 1-65535";
  if (!inv.global.default_username) return "Global username is required";
  const ids = new Set();
  for (const nvr of inv.nvrs) {
    if (!nvr.id) return "NVR ID is required";
    if (ids.has(nvr.id)) return `Duplicate NVR ID: ${nvr.id}`;
    ids.add(nvr.id);
    if (!nvr.ip) return `${nvr.id}: IP address is required`;
    if (!ipRe.test(nvr.ip)) return `${nvr.id}: Invalid IP format (${nvr.ip})`;
    if (nvr.channels < 1 || nvr.channels > 256) return `${nvr.id}: Channels must be 1-256`;
  }
  return null;
}

// ── Save flow ───────────────────────────────────────────────────────────────

export async function saveSettings() {
  state.prefs.maxRetries = parseInt(dom.settingsMaxRetries.value);
  state.prefs.retryDelay = Math.max(1, parseInt(dom.settingsRetryDelay.value) || 10);
  state.prefs.maxConcurrent = Math.max(1, Math.min(32, parseInt(dom.settingsMaxConcurrent.value) || 4));
  if (isNaN(state.prefs.maxRetries)) state.prefs.maxRetries = 3;
  savePrefs();

  const inv = harvestInventory();
  const err = validateInventory(inv);
  if (err) { setSettingsStatus(err, true); return; }

  // Pre-flight: test all enabled NVRs to avoid IP bans on bad credentials
  const enabledCount = inv.nvrs.filter(n => n.enabled !== false).length;
  if (enabledCount > 0) {
    dom.settingsSaveBtn.disabled = true;
    setSettingsStatus(`Testing ${enabledCount} NVRs before save...`);
    try {
      const testRes = await fetch("/api/test-all-nvrs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(inv),
      });
      const testData = await testRes.json();
      if (testData.failed_count > 0) {
        const failedNames = testData.results
          .filter(r => r.ok === false)
          .map(r => `${r.id}: ${r.message}`)
          .join("\n");
        const proceed = confirm(
          `${testData.failed_count} NVR(s) failed credential test:\n\n${failedNames}\n\n` +
          "Saving with wrong credentials may trigger IP bans on Dahua/Hikvision NVRs.\n\n" +
          "Click OK to save anyway, or Cancel to fix first."
        );
        if (!proceed) {
          try {
            await fetch("/api/inventory", {
              method: "PATCH",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(inv),
            });
            setSettingsStatus("Passwords saved (no restart — NVRs unchanged)", false);
          } catch (_) {
            setSettingsStatus("Save cancelled", true);
          }
          dom.settingsSaveBtn.disabled = false;
          return;
        }
      }
    } catch (e) {
      // Continue if test endpoint itself errors
    }
  }

  dom.settingsSaveBtn.disabled = true;
  setSettingsStatus("Saving...");
  try {
    const res = await fetch("/api/inventory", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(inv),
    });
    if (res.status === 401) { location.href = "/login"; return; }
    const data = await res.json();
    if (!res.ok) {
      setSettingsStatus(data.error || "Save failed", true);
      return;
    }
    setSettingsStatus(data.message || "Saved & applied");
    setTimeout(async () => {
      await fetchInventory();
      await fetchCameras();
      reconnectAllVisible();
    }, 1500);
  } catch (e) {
    setSettingsStatus("Network error: " + e, true);
  } finally {
    dom.settingsSaveBtn.disabled = false;
  }
}

export async function forceRestart() {
  dom.settingsRestartBtn.disabled = true;
  setSettingsStatus("Restarting MediaMTX...");
  try {
    const res = await fetch("/api/restart", { method: "POST" });
    const data = await res.json();
    if (!res.ok) { setSettingsStatus(data.error || "Restart failed", true); return; }
    setSettingsStatus("MediaMTX restarted");
    setTimeout(() => reconnectAllVisible(), 1500);
  } catch (e) {
    setSettingsStatus("Network error: " + e, true);
  } finally {
    dom.settingsRestartBtn.disabled = false;
  }
}

// ── Health & test-all ───────────────────────────────────────────────────────

export async function checkNvrHealth() {
  dom.settingsHealthBtn.disabled = true;
  dom.settingsHealthBtn.textContent = "Checking...";
  dom.settingsHealthStatus.textContent = "";
  dom.settingsHealthStatus.className = "";
  try {
    const res = await fetch("/api/health", { method: "POST" });
    const data = await res.json();
    if (!res.ok) {
      dom.settingsHealthStatus.textContent = data.error || "Health check failed";
      dom.settingsHealthStatus.className = "err";
      return;
    }
    const results = data.results || [];
    let ok = 0, fail = 0, disabled = 0;
    dom.settingsNvrBody.querySelectorAll("tr").forEach(tr => {
      const id = (tr.querySelector('[data-field="id"]') || {}).value;
      const r = results.find(x => x.id === id);
      const dot = tr.querySelector(".nvr-health-dot");
      if (!dot || !r) return;
      dot.classList.remove("hidden", "ok", "fail", "disabled");
      if (r.message === "Disabled") {
        dot.classList.add("disabled");
        dot.title = "Disabled";
        disabled++;
      } else if (r.ok) {
        dot.classList.add("ok");
        dot.title = "Reachable";
        ok++;
      } else {
        dot.classList.add("fail");
        dot.title = r.message;
        fail++;
      }
    });
    let msg = `${ok} reachable`;
    if (fail > 0) msg += `, ${fail} unreachable`;
    if (disabled > 0) msg += `, ${disabled} disabled`;
    dom.settingsHealthStatus.textContent = msg;
    dom.settingsHealthStatus.className = fail > 0 ? "err" : "ok";
  } catch (e) {
    dom.settingsHealthStatus.textContent = "Network error";
    dom.settingsHealthStatus.className = "err";
  } finally {
    dom.settingsHealthBtn.disabled = false;
    dom.settingsHealthBtn.textContent = "Check Health";
  }
}

export async function testAllNvrs() {
  const inv = harvestInventory();
  const enabled = inv.nvrs.filter(n => n.enabled !== false);
  if (enabled.length === 0) {
    dom.settingsHealthStatus.textContent = "No enabled NVRs to test";
    return;
  }
  dom.settingsTestAllBtn.disabled = true;
  dom.settingsTestAllBtn.textContent = `Testing 0/${enabled.length}...`;
  dom.settingsHealthStatus.textContent = "";

  try {
    const res = await fetch("/api/test-all-nvrs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(inv),
    });
    const data = await res.json();
    const results = data.results || [];
    let ok = 0, fail = 0;

    dom.settingsNvrBody.querySelectorAll("tr").forEach(tr => {
      const id = (tr.querySelector('[data-field="id"]') || {}).value;
      const r = results.find(x => x.id === id);
      if (!r) return;
      const btn = tr.querySelector(".test");
      if (r.ok === true) {
        ok++;
        if (btn) { btn.textContent = "OK"; btn.style.color = "#4caf50"; btn.style.borderColor = "#4caf50"; }
        clearBanTimer(tr);
      } else if (r.ok === false) {
        fail++;
        if (btn) { btn.textContent = "Fail"; btn.style.color = "#f44336"; btn.style.borderColor = "#f44336"; }
        if (r.banned_until) showBanTimer(tr, r.banned_until);
      }
      setTimeout(() => {
        if (btn) { btn.textContent = "Test"; btn.style.color = ""; btn.style.borderColor = ""; }
      }, 6000);
    });

    let msg = `${ok} passed`;
    if (fail > 0) msg += `, ${fail} failed`;
    dom.settingsHealthStatus.textContent = msg;
    dom.settingsHealthStatus.className = fail > 0 ? "err" : "ok";
  } catch (e) {
    dom.settingsHealthStatus.textContent = "Network error";
    dom.settingsHealthStatus.className = "err";
  } finally {
    dom.settingsTestAllBtn.disabled = false;
    dom.settingsTestAllBtn.textContent = "Test All";
  }
}

export async function clearAllBans() {
  dom.settingsClearBansBtn.disabled = true;
  try {
    const res = await fetch("/api/lockouts", { method: "DELETE" });
    const data = await res.json();
    if (data.ok) {
      dom.settingsNvrBody.querySelectorAll(".nvr-ban-timer").forEach(el => {
        if (el._timer) clearTimeout(el._timer);
        el.remove();
      });
      dom.settingsHealthStatus.textContent = `Cleared ${data.cleared} ban(s)`;
      dom.settingsHealthStatus.className = "ok";
    }
  } catch (e) {
    dom.settingsHealthStatus.textContent = "Failed to clear bans";
    dom.settingsHealthStatus.className = "err";
  } finally {
    dom.settingsClearBansBtn.disabled = false;
  }
}

// ── Import NVRs ─────────────────────────────────────────────────────────────

export function importNvrs() {
  const raw = dom.importTextarea.value.trim();
  if (!raw) { dom.importStatus.textContent = "Nothing to import"; dom.importStatus.className = "err"; return; }

  let nvrs = [];
  try {
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed)) {
      nvrs = parsed;
    } else {
      dom.importStatus.textContent = "JSON must be an array of NVR objects";
      dom.importStatus.className = "err";
      return;
    }
  } catch (_) {
    const lines = raw.split("\n").map(l => l.trim()).filter(Boolean);
    const header = lines[0].toLowerCase();
    const hasHeader = header.includes("id") && header.includes("ip");
    const dataLines = hasHeader ? lines.slice(1) : lines;
    for (const line of dataLines) {
      const parts = line.split(",").map(s => s.trim());
      if (parts.length < 3) continue;
      nvrs.push({
        id: parts[0],
        label: parts[1] || parts[0],
        ip: parts[2],
        channels: parseInt(parts[3]) || 1,
        password: parts[4] || "",
        vendor: parts[5] || "dahua",
      });
    }
  }

  if (nvrs.length === 0) {
    dom.importStatus.textContent = "No valid NVRs found in input";
    dom.importStatus.className = "err";
    return;
  }

  const ipRe = /^(\d{1,3}\.){3}\d{1,3}$/;
  const errors = [];
  nvrs.forEach((n, i) => {
    if (!n.id) errors.push(`Row ${i + 1}: missing id`);
    if (!n.ip || !ipRe.test(n.ip)) errors.push(`Row ${i + 1}: invalid IP (${n.ip || "empty"})`);
  });
  if (errors.length > 0) {
    dom.importStatus.textContent = errors.slice(0, 3).join("; ");
    dom.importStatus.className = "err";
    return;
  }

  nvrs.forEach(n => {
    appendNvrRow({
      id: n.id,
      label: n.label || n.id,
      ip: n.ip,
      channels: parseInt(n.channels) || 1,
      password: n.password || "",
      vendor: n.vendor || "dahua",
      enabled: n.enabled !== false,
      stream_source: n.stream_source || "",
    });
  });
  updateNvrCount();
  dom.importStatus.textContent = `Imported ${nvrs.length} NVRs`;
  dom.importStatus.className = "ok";
  setTimeout(() => dom.importDialog.classList.add("hidden"), 1500);
}

// ── Events log ──────────────────────────────────────────────────────────────

export async function openEventsLog() {
  dom.eventsDialog.classList.remove("hidden");
  dom.eventsList.innerHTML = "<div style='color:#666'>Loading...</div>";
  try {
    const res = await fetch("/api/events?limit=200");
    const data = await res.json();
    const events = data.events || [];
    if (events.length === 0) {
      dom.eventsList.innerHTML = "<div style='color:#666'>No events recorded yet</div>";
      return;
    }
    dom.eventsList.innerHTML = events.map(e => {
      const d = new Date(e.ts * 1000);
      const time = d.toLocaleString();
      return `<div class="event-item">` +
        `<span class="event-time">${esc(time)}</span>` +
        `<span class="event-nvr">${esc(e.nvr_id)}</span>` +
        `<span class="event-type ${esc(e.event)}">${esc(e.event)}</span>` +
        `<span>${esc(e.message)}</span>` +
      `</div>`;
    }).join("");
  } catch (e) {
    dom.eventsList.innerHTML = "<div style='color:#f44336'>Failed to load events</div>";
  }
}

// ── Auth ────────────────────────────────────────────────────────────────────

export async function logout() {
  const { logout: apiLogout } = await import("./api.js");
  await apiLogout();
}

export async function changePassword() {
  const cur = dom.settingsCurPw.value;
  const newPw = dom.settingsNewPw.value;
  if (!cur || !newPw) {
    dom.settingsChpwStatus.textContent = "Both fields required";
    dom.settingsChpwStatus.className = "err";
    return;
  }
  if (newPw.length < 8) {
    dom.settingsChpwStatus.textContent = "Min 8 characters";
    dom.settingsChpwStatus.className = "err";
    return;
  }
  try {
    const { changePassword: apiChange } = await import("./api.js");
    await apiChange(cur, newPw);
    dom.settingsChpwStatus.textContent = "Password changed";
    dom.settingsChpwStatus.className = "ok";
    dom.settingsCurPw.value = "";
    dom.settingsNewPw.value = "";
  } catch (e) {
    dom.settingsChpwStatus.textContent = String(e.message || e) || "Network error";
    dom.settingsChpwStatus.className = "err";
  }
}
