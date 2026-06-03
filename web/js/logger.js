/**
 * Diagnostic logger — buffers entries, mirrors them to the DevTools console,
 * and POSTs the buffer to /api/v1/client-log every few seconds so server-side
 * logs include the browser's view (WebRTC negotiation, ICE state, WHEP
 * responses, stalls, render-cycle diagnostics, NVR-add flow).
 *
 * Server endpoint is unauthenticated (so login-page failures are captured).
 * Flush failures are swallowed so logging never breaks the app.
 */

import { CONFIG } from "./config.js";

const _ts = () => new Date().toISOString().slice(11, 23); // HH:MM:SS.mmm

const _state = {
  buf: [],
  timer: null,
  // Flush early if the buffer grows past this — keeps memory bounded if a
  // tight loop starts firing logs.
  MAX: 200,
  // Periodic flush cadence (ms).
  FLUSH_MS: 3000,
};

// Console mirroring — only WARN/ERROR reach the console. INFO/DEBUG still go
// to the buffer + server POST, but mirroring every one to DevTools is a real
// perf drag with dozens of cameras each logging connect/ice/stream events.
// Never throws.
function _mirror(level, path, msg, detail) {
  if (level !== "ERROR" && level !== "WARNING") return;
  try {
    const tag = path ? `[${path}]` : "";
    const line = detail ? `${tag} ${msg} | ${detail}` : `${tag} ${msg}`;
    (level === "ERROR" ? console.error : console.warn).call(console, `dss ${line}`);
  } catch (_) {}
}

function _push(level, path, msg, detail) {
  const entry = { level, path: path || "", msg: String(msg || ""), detail: String(detail || ""), ts: _ts() };
  _state.buf.push(entry);
  _mirror(level, path, msg, detail);
  if (_state.buf.length >= _state.MAX) {
    flush();
    return;
  }
  if (!_state.timer) {
    _state.timer = setTimeout(() => flush(), _state.FLUSH_MS);
  }
}

export function flush() {
  if (_state.timer) { clearTimeout(_state.timer); _state.timer = null; }
  if (_state.buf.length === 0) return;
  const batch = _state.buf.splice(0, _state.buf.length);
  // Fire-and-forget; we don't await, and we don't surface errors.
  // keepalive=true lets the POST survive a page unload (matters for
  // beforeunload flush).
  try {
    fetch(CONFIG.backendBase + "/client-log", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ entries: batch }),
      keepalive: true,
    }).catch(() => {});
  } catch (_) {
    // Swallow — logging must never break the app.
  }
}

export const dlog = {
  debug: (path, msg, detail) => _push("DEBUG",   path, msg, detail),
  info:  (path, msg, detail) => _push("INFO",    path, msg, detail),
  warn:  (path, msg, detail) => _push("WARNING", path, msg, detail),
  error: (path, msg, detail) => _push("ERROR",   path, msg, detail),
  flush,
};

// Capture uncaught errors and unhandled rejections so the server sees them.
if (typeof window !== "undefined") {
  window.addEventListener("error", (e) => {
    try {
      const src = e.filename ? `${e.filename}:${e.lineno}:${e.colno}` : "";
      dlog.error("", "window-error", `${e.message || "?"} ${src}`);
    } catch (_) {}
  });
  window.addEventListener("unhandledrejection", (e) => {
    try {
      const reason = e.reason && (e.reason.stack || e.reason.message || String(e.reason));
      dlog.error("", "unhandled-rejection", String(reason || "?").slice(0, 1000));
    } catch (_) {}
  });
}
