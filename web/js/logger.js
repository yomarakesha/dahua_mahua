/**
 * Diagnostic logger — buffers entries and flushes to server every 5s.
 * Captures WebRTC negotiation, ICE state, WHEP responses, stalls, errors.
 */

const _ts = () => new Date().toISOString().slice(11, 23); // HH:MM:SS.mmm

const _state = {
  buf: [],
  timer: null,
  MAX: 200,
};

function _push(level, path, msg, detail) {
  _state.buf.push({ level, path: path || "", msg, detail: detail || "", ts: _ts() });
  if (_state.buf.length >= _state.MAX) flush();
  if (!_state.timer) {
    _state.timer = setTimeout(() => flush(), 5000);
  }
}

export function flush() {
  if (_state.timer) { clearTimeout(_state.timer); _state.timer = null; }
  if (_state.buf.length === 0) return;
  const batch = _state.buf.splice(0);
  fetch("/api/client-log", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(batch),
  }).catch(() => {}); // best-effort
}

export const dlog = {
  debug: (path, msg, detail) => _push("DEBUG",   path, msg, detail),
  info:  (path, msg, detail) => _push("INFO",    path, msg, detail),
  warn:  (path, msg, detail) => _push("WARNING", path, msg, detail),
  error: (path, msg, detail) => _push("ERROR",   path, msg, detail),
  flush,
};
