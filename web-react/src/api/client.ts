/**
 * JWT-aware fetch wrapper for the FastAPI backend. Mirrors web/js/api.js:
 * stashes the token in localStorage, adds Bearer to every call, and on 401
 * clears the session and redirects to /login.
 */
import { CONFIG, STORAGE } from "@/lib/config";
import type { Me, TokenResponse } from "./types";

export function getToken(): string | null {
  return localStorage.getItem(STORAGE.token);
}
export function setToken(tok: string | null) {
  if (tok) localStorage.setItem(STORAGE.token, tok);
  else localStorage.removeItem(STORAGE.token);
}
export function getMe(): Me | null {
  const raw = localStorage.getItem(STORAGE.me);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as Me;
  } catch {
    // A corrupt/truncated value would otherwise throw during AuthProvider init
    // → blank-screen app with no recovery. Clear it and treat as logged-out.
    localStorage.removeItem(STORAGE.me);
    return null;
  }
}
export function setMe(me: Me | null) {
  if (me) localStorage.setItem(STORAGE.me, JSON.stringify(me));
  else localStorage.removeItem(STORAGE.me);
}
export function isAdmin(): boolean {
  return getMe()?.role === "admin";
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

interface ReqOpts {
  body?: unknown;
  query?: Record<string, string | number | boolean | undefined | null>;
}

async function request<T>(method: string, path: string, opts: ReqOpts = {}): Promise<T> {
  const url = new URL(CONFIG.backendBase + path, window.location.origin);
  if (opts.query) {
    for (const [k, v] of Object.entries(opts.query)) {
      if (v !== undefined && v !== null) url.searchParams.set(k, String(v));
    }
  }
  const tok = getToken();
  const init: RequestInit = {
    method,
    headers: {
      ...(tok ? { Authorization: "Bearer " + tok } : {}),
      ...(opts.body ? { "Content-Type": "application/json" } : {}),
    },
  };
  if (opts.body) init.body = JSON.stringify(opts.body);

  const res = await fetch(url.toString(), init);

  if (res.status === 401) {
    setToken(null);
    setMe(null);
    if (!location.hash.startsWith("#/login")) location.hash = "#/login";
    throw new ApiError(401, "unauthenticated");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = j.detail || detail;
    } catch {
      /* non-json error */
    }
    throw new ApiError(res.status, String(detail));
  }
  if (res.status === 204) return null as T;
  return (await res.json()) as T;
}

export const http = {
  get: <T>(p: string, query?: ReqOpts["query"]) => request<T>("GET", p, { query }),
  post: <T>(p: string, body?: unknown) => request<T>("POST", p, { body }),
  patch: <T>(p: string, body?: unknown) => request<T>("PATCH", p, { body }),
  del: <T>(p: string) => request<T>("DELETE", p),
};

// ── Auth (login is special: public, sets token, then fetches profile) ────────

export async function login(username: string, password: string) {
  const res = await fetch(CONFIG.backendBase + "/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail || detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, String(detail));
  }
  const data = (await res.json()) as TokenResponse;
  setToken(data.access_token);
  // Fetch the profile directly (not via request()) so a transient 401 here
  // doesn't trip the global 401 handler — which would wipe the token we just set
  // and yank the route to #/login, fighting the login page's own navigation.
  const meRes = await fetch(CONFIG.backendBase + "/auth/me", {
    headers: { Authorization: "Bearer " + data.access_token },
  });
  if (!meRes.ok) {
    setToken(null);
    throw new ApiError(meRes.status, "Signed in, but could not load your profile. Try again.");
  }
  const me = (await meRes.json()) as Me;
  setMe(me);
  return { token: data, me, mustChange: data.must_change_password };
}

export async function logout() {
  try {
    await request("POST", "/auth/logout");
  } catch {
    /* ignore */
  }
  setToken(null);
  setMe(null);
  // Hard reload so React state (AuthProvider's in-memory `me`) re-initialises
  // from the now-cleared storage — otherwise the user appears still logged in.
  window.location.hash = "#/login";
  window.location.reload();
}
