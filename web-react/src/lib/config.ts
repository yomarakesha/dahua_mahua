/**
 * Runtime endpoints — derived from the page host so a client browser on any
 * machine reaches the server it loaded the app from. Mirrors the legacy
 * web/js/config.js behaviour: backend on :8000, go2rtc on :1984.
 *
 * The video relay is go2rtc buffered-MSE (the fix that, with hardware decode,
 * gives smooth 4MP). The browser talks to the backend for inventory and to
 * go2rtc directly for media (fan-out), never proxying media through the API.
 */
const host = window.location.hostname || "localhost";
const wsProto = window.location.protocol === "https:" ? "wss:" : "ws:";
const httpProto = window.location.protocol === "https:" ? "https:" : "http:";

export const CONFIG = {
  backendBase: `${httpProto}//${host}:8000/api/v1`,
  go2rtcWsBase: `${wsProto}//${host}:1984`,
  gridPresets: [1, 4, 9, 16, 25, 36, 64] as const,
  patrolIntervals: [5, 10, 15, 30, 60] as const,
} as const;

export const STORAGE = {
  token: "dss_token",
  me: "dss_me",
} as const;
