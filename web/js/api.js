/**
 * Thin JWT-aware fetch wrapper for the new FastAPI backend.
 *
 * The backend issues HS256 access tokens via POST /api/v1/auth/login. We
 * stash the token in localStorage and add it to the Authorization header of
 * every backend call. On 401 we wipe the token and bounce to /login.html.
 *
 * Note: WebRTC (WHEP) and HLS requests still go DIRECTLY to MediaMTX from
 * the browser — they don't pass through this wrapper. That's intentional:
 * proxying media through the backend would defeat the whole fan-out point.
 * The backend only hands out the URLs; the player connects to them itself.
 */
import { CONFIG } from "./config.js";
import { dlog } from "./logger.js";

const TOKEN_KEY = "dss_token";
const ME_KEY = "dss_me";

export function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(tok) {
  if (tok) localStorage.setItem(TOKEN_KEY, tok);
  else localStorage.removeItem(TOKEN_KEY);
}

export function getMe() {
  const raw = localStorage.getItem(ME_KEY);
  return raw ? JSON.parse(raw) : null;
}

export function setMe(me) {
  if (me) localStorage.setItem(ME_KEY, JSON.stringify(me));
  else localStorage.removeItem(ME_KEY);
}

export function isAdmin() {
  const me = getMe();
  return !!me && me.role === "admin";
}

function authHeaders() {
  const tok = getToken();
  return tok ? { "Authorization": "Bearer " + tok } : {};
}

async function request(method, path, { body, query } = {}) {
  const url = new URL(CONFIG.backendBase + path, window.location.origin);
  if (query) for (const [k, v] of Object.entries(query)) {
    if (v !== undefined && v !== null) url.searchParams.set(k, String(v));
  }
  const init = {
    method,
    headers: { ...authHeaders(), ...(body ? { "Content-Type": "application/json" } : {}) },
  };
  if (body) init.body = JSON.stringify(body);

  const t0 = performance.now();
  let res;
  try {
    res = await fetch(url.toString(), init);
  } catch (e) {
    dlog.error("", "api-network-error", `${method} ${path} ${(performance.now() - t0).toFixed(0)}ms: ${String(e)}`);
    throw e;
  }
  const dt = (performance.now() - t0).toFixed(0);

  if (res.status === 401) {
    dlog.warn("", "api-401", `${method} ${path} ${dt}ms — bouncing to login`);
    setToken(null);
    setMe(null);
    if (!location.pathname.endsWith("/login.html")) {
      location.href = "/login.html";
    }
    throw new Error("unauthenticated");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try { const j = await res.json(); detail = j.detail || detail; } catch (_) {}
    dlog.error("", "api-error", `${method} ${path} → ${res.status} ${dt}ms: ${String(detail).slice(0, 300)}`);
    throw new Error(`${method} ${path} → ${res.status}: ${detail}`);
  }
  dlog.debug("", "api-ok", `${method} ${path} → ${res.status} ${dt}ms`);
  if (res.status === 204) return null;
  return res.json();
}

// ── Auth ────────────────────────────────────────────────────────────────────

export async function login(username, password) {
  dlog.info("", "login-attempt", `user=${username}`);
  const t0 = performance.now();
  let res;
  try {
    // Public endpoint — no auth header. Browser will set Content-Type.
    res = await fetch(CONFIG.backendBase + "/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
  } catch (e) {
    dlog.error("", "login-network-error", `user=${username} ${(performance.now() - t0).toFixed(0)}ms: ${String(e)}`);
    throw e;
  }
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) {}
    dlog.warn("", "login-fail", `user=${username} status=${res.status} detail=${String(detail).slice(0, 200)}`);
    throw new Error(detail);
  }
  const data = await res.json();
  setToken(data.access_token);
  // Pull profile so we know admin vs operator without re-decoding the JWT.
  const me = await request("GET", "/auth/me");
  setMe(me);
  dlog.info("", "login-ok",
    `user=${me.username} role=${me.role} mustChange=${data.must_change_password} dt=${(performance.now() - t0).toFixed(0)}ms`);
  return { token: data, me, mustChange: data.must_change_password };
}

export async function logout() {
  try { await request("POST", "/auth/logout"); } catch (_) {}
  setToken(null);
  setMe(null);
  location.href = "/login.html";
}

export async function changePassword(currentPassword, newPassword) {
  return request("POST", "/auth/change-password", {
    body: { current_password: currentPassword, new_password: newPassword },
  });
}

// ── Inventory ───────────────────────────────────────────────────────────────

export const listCameras = (opts) => request("GET", "/cameras", { query: opts });
export const listNvrs = () => request("GET", "/nvrs");
export const listRegions = () => request("GET", "/regions");
export const listEvents = (params) => request("GET", "/events", { query: params });

export const createNvr = (body) => request("POST", "/nvrs", { body });
export const updateNvr = (id, body) => request("PATCH", `/nvrs/${id}`, { body });
export const deleteNvr = (id) => request("DELETE", `/nvrs/${id}`);
export const testNvr = (id) => request("POST", `/nvrs/${id}/test`);
export const setNvrChannels = (id, count, prune = false) =>
  request("POST", `/nvrs/${id}/set-channels`, { body: { count, prune } });
export const importCameraIps = (id) =>
  request("POST", `/nvrs/${id}/import-camera-ips`);
export const healthAllNvrs = () => request("GET", "/nvrs/health");
export const createCamera = (body) => request("POST", "/cameras", { body });
export const updateCamera = (id, body) => request("PATCH", `/cameras/${id}`, { body });
export const deleteCamera = (id) => request("DELETE", `/cameras/${id}`);

// ── MediaMTX (admin) ────────────────────────────────────────────────────────

export const reconcileMediamtx = () => request("POST", "/mediamtx/reconcile");
export const mediamtxHealth = () => request("GET", "/mediamtx/health");

// ── Discovery (admin) ───────────────────────────────────────────────────────

export const discoveryScan = (body) =>
  request("POST", "/discovery/scan", { body });
export const discoveryImport = (body) =>
  request("POST", "/discovery/import", { body });
