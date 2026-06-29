/**
 * Runtime endpoints — derived from the page host so a client browser on any
 * machine reaches the server it loaded the app from. Mirrors the legacy
 * web/js/config.js behaviour: backend on :8000, go2rtc on :1984.
 *
 * The video relay is go2rtc buffered-MSE (the fix that, with hardware decode,
 * gives smooth 4MP). The browser talks to the backend for inventory and to
 * go2rtc directly for media (fan-out), never proxying media through the API.
 *
 * Two delivery modes:
 *  - HTTP (dev / legacy :8080): backend on :8000, go2rtc on :1984, direct ports.
 *  - HTTPS (the TLS reverse proxy, Caddy on :8443): ONE secure origin so the
 *    browser gets a secure context (required for WebCodecs) with no mixed-content
 *    or CORS. Everything is same-origin and path-routed by the proxy:
 *      /api/*     → backend     (kept, not stripped → backend sees /api/v1/…)
 *      /go2rtc/*  → go2rtc WS    (prefix stripped by Caddy → /api/ws?src=…)
 * We pick by page protocol, so the same build works behind either.
 *
 * Dev override: set VITE_SERVER_HOST (e.g. 10.10.1.152) to run the frontend
 * locally (`npm run dev`, HTTP) against a REMOTE server's backend + go2rtc, so
 * UI/player changes are testable without build-and-deploy.
 */
const viteEnv = (import.meta as unknown as { env?: Record<string, string> }).env;
const isHttps = window.location.protocol === "https:";
const host =
  viteEnv?.VITE_SERVER_HOST ||
  window.location.hostname ||
  "localhost";
const wsProto = isHttps ? "wss:" : "ws:";
const httpProto = isHttps ? "https:" : "http:";

export const CONFIG = {
  backendBase: isHttps
    ? `${window.location.origin}/api/v1`
    : `${httpProto}//${host}:8000/api/v1`,
  go2rtcWsBase: isHttps
    ? `wss://${window.location.host}/go2rtc`
    : `${wsProto}//${host}:1984`,
  patrolIntervals: [5, 10, 15, 30, 60] as const,
} as const;

export const STORAGE = {
  token: "dss_token",
  me: "dss_me",
} as const;
