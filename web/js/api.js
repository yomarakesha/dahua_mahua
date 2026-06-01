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

  const res = await fetch(url.toString(), init);
  if (res.status === 401) {
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
    throw new Error(`${method} ${path} → ${res.status}: ${detail}`);
  }
  if (res.status === 204) return null;
  return res.json();
}

// ── Auth ────────────────────────────────────────────────────────────────────

export async function login(username, password) {
  // Public endpoint — no auth header. Browser will set Content-Type.
  const res = await fetch(CONFIG.backendBase + "/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  const data = await res.json();
  setToken(data.access_token);
  // Pull profile so we know admin vs operator without re-decoding the JWT.
  const me = await request("GET", "/auth/me");
  setMe(me);
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

export const listCameras = () => request("GET", "/cameras");
export const listNvrs = () => request("GET", "/nvrs");
export const listRegions = () => request("GET", "/regions");
export const listEvents = (params) => request("GET", "/events", { query: params });

export const createNvr = (body) => request("POST", "/nvrs", { body });
export const updateNvr = (id, body) => request("PATCH", `/nvrs/${id}`, { body });
export const deleteNvr = (id) => request("DELETE", `/nvrs/${id}`);
export const testNvr = (id) => request("POST", `/nvrs/${id}/test`);
export const healthAllNvrs = () => request("POST", "/nvrs/health");
export const updateCamera = (id, body) => request("PATCH", `/cameras/${id}`, { body });

// ── Streams ─────────────────────────────────────────────────────────────────
/**
 * Backend returns { camera_id, quality, path, webrtc_whep_url, hls_url }.
 * Path name matches what MediaMTX expects on its WHEP/HLS endpoints.
 */
export const getStreamUrls = (cameraId, quality = "sub") =>
  request("GET", `/streams/${cameraId}`, { query: { quality } });

// ── MediaMTX (admin) ────────────────────────────────────────────────────────

export const reconcileMediamtx = () => request("POST", "/mediamtx/reconcile");
export const mediamtxHealth = () => request("GET", "/mediamtx/health");

// ── Discovery (admin) ───────────────────────────────────────────────────────

export const discoveryScan = (body) =>
  request("POST", "/discovery/scan", { body });
export const discoveryImport = (body) =>
  request("POST", "/discovery/import", { body });
