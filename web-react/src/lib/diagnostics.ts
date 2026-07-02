/**
 * Frontend observability kit — an in-memory ring of the most recent log lines
 * plus a crash-safe shipper that POSTs them to the backend client-log receiver
 * (`backend/app/routers/client_log.py`).
 *
 * Why this exists: this project's hardest field bugs (black video, freezes,
 * codec mismatch) were only diagnosable with live DevTools open. When an
 * operator hits one in the field we get nothing. This module keeps the last
 * ~300 log/console/event lines in memory and, on a crash or uncaught error,
 * beacons them to the server so `dss.client` has the trail.
 *
 * Contract with the receiver (read from client_log.py):
 *   POST {backendBase}/client-log  →  { entries: ClientLogEntry[] }
 *   ClientLogEntry = { level: "DEBUG"|"INFO"|"WARNING"|"ERROR", ts?, path?, msg, detail? }
 *   Caps: ≤500 entries/request, each field ≤2000 chars, ≤30 requests / 10 s per IP.
 * We ride the extra ship context (reason/url/ua/sessionIds) as a leading meta
 * entry because the receiver only accepts `entries`.
 *
 * Diagnostics must NEVER throw or recurse into itself — every public function
 * swallows its own errors and the console patch calls through to the originals.
 */
import { CONFIG } from "@/lib/config";

export type DiagLevel = "info" | "warn" | "error";

export interface DiagEntry {
  ts: string; // client-side HH:MM:SS.mmm — advisory only
  level: DiagLevel;
  msg: string;
}

/** Backend ClientLogEntry shape (client_log.py). */
interface BackendEntry {
  level: "DEBUG" | "INFO" | "WARNING" | "ERROR";
  ts: string;
  path: string;
  msg: string;
  detail: string;
}

const RING_CAP = 300;
const MSG_CAP = 500; // per ring entry (backend allows 2000, we keep it tighter)
const FIELD_CAP = 2000; // backend _MAX_FIELD_LEN
const MAX_ENTRIES_PER_SHIP = 500; // backend _MAX_ENTRIES_PER_REQUEST
const SHIP_THROTTLE_MS = 30_000;
const MAX_SESSION_IDS = 5;

const ring: DiagEntry[] = [];
const sessionIds: string[] = [];
let lastShipAt = 0;
let droppedShips = 0;
let consolePatched = false;

function nowTs(): string {
  const d = new Date();
  const p = (n: number, w = 2) => String(n).padStart(w, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}.${p(d.getMilliseconds(), 3)}`;
}

function truncate(s: string, cap: number): string {
  return s.length > cap ? s.slice(0, cap) : s;
}

function push(level: DiagLevel, msg: string): void {
  ring.push({ ts: nowTs(), level, msg: truncate(msg, MSG_CAP) });
  if (ring.length > RING_CAP) ring.shift();
}

/** Best-effort stringify of console args — never throws. */
function argsToString(args: unknown[]): string {
  return args
    .map((a) => {
      if (typeof a === "string") return a;
      if (a instanceof Error) return `${a.name}: ${a.message}`;
      try {
        return JSON.stringify(a);
      } catch {
        return String(a);
      }
    })
    .join(" ");
}

/**
 * Record a structured app event (player transitions, WS closes, …). Works even
 * when the console patch is not installed — the app calls this directly.
 */
export function recordEvent(category: string, msg: string): void {
  push("info", `[${category}] ${msg}`);
}

/** Remember a playback session id so it rides along on the next ship. */
export function registerSessionId(id: string): void {
  if (!id || sessionIds.includes(id)) return;
  sessionIds.push(id);
  while (sessionIds.length > MAX_SESSION_IDS) sessionIds.shift();
}

/**
 * Patch console.warn + console.error to ALSO record into the ring, calling
 * through to the originals. Idempotent — safe to call once at startup.
 */
export function installConsoleCapture(): void {
  if (consolePatched || typeof console === "undefined") return;
  consolePatched = true;
  const origWarn = console.warn.bind(console);
  const origError = console.error.bind(console);
  console.warn = (...args: unknown[]) => {
    try {
      push("warn", argsToString(args));
    } catch {
      /* diagnostics must never break logging */
    }
    origWarn(...args);
  };
  console.error = (...args: unknown[]) => {
    try {
      push("error", argsToString(args));
    } catch {
      /* diagnostics must never break logging */
    }
    origError(...args);
  };
}

function levelToBackend(l: DiagLevel): BackendEntry["level"] {
  return l === "warn" ? "WARNING" : l === "error" ? "ERROR" : "INFO";
}

/** Ship reasons that must NEVER be throttled: the page may be about to die
 *  (crash/unload), so this is the last chance to get the evidence out. */
const CRASH_REASONS = new Set(["react-crash", "uncaught"]);

/**
 * Ship the current ring to the backend client-log endpoint. Crash-safe:
 * uses navigator.sendBeacon when available (with a fetch(keepalive) fallback
 * when the beacon is rejected, e.g. over the browser's beacon size budget),
 * else fetch(keepalive). Throttled to at most one ship per 30 s — EXCEPT
 * crash-class reasons (react-crash / uncaught), which always ship: a crash is
 * often the last event of the page's life and must not be starved by an
 * earlier routine ship. The ring is NOT cleared — it keeps rolling.
 *
 * Returns true if a ship was attempted, false if throttled.
 */
export function shipLogs(reason: string): boolean {
  const now = Date.now();
  if (!CRASH_REASONS.has(reason) && now - lastShipAt < SHIP_THROTTLE_MS) {
    droppedShips += 1;
    return false;
  }
  lastShipAt = now;

  const url = typeof location !== "undefined" ? location.hash : "";
  const ua = typeof navigator !== "undefined" ? navigator.userAgent : "";

  const meta: BackendEntry = {
    level: "ERROR",
    ts: nowTs(),
    path: "diag",
    msg: truncate(`ship reason=${reason} url=${url}`, FIELD_CAP),
    detail: truncate(
      `ua=${ua} sessionIds=[${sessionIds.join(",")}] droppedShips=${droppedShips}`,
      FIELD_CAP,
    ),
  };
  droppedShips = 0;

  const entries: BackendEntry[] = [meta];
  for (const e of ring) {
    entries.push({
      level: levelToBackend(e.level),
      ts: e.ts,
      path: "",
      msg: truncate(e.msg, FIELD_CAP),
      detail: "",
    });
    if (entries.length >= MAX_ENTRIES_PER_SHIP) break;
  }

  const endpoint = `${CONFIG.backendBase}/client-log`;
  const body = JSON.stringify({ entries });

  try {
    let sent = false;
    if (typeof navigator !== "undefined" && typeof navigator.sendBeacon === "function") {
      const blob = new Blob([body], { type: "application/json" });
      // sendBeacon returns false without sending when the payload exceeds the
      // browser's beacon budget (~64KB) — fall through to fetch in that case.
      sent = navigator.sendBeacon(endpoint, blob);
    }
    if (!sent && typeof fetch === "function") {
      void fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body,
        keepalive: true,
      }).catch(() => {
        /* best-effort; nowhere to report a failed ship */
      });
    }
  } catch {
    /* diagnostics must never throw into the caller (crash/error handlers) */
  }
  return true;
}

// ── Test-only helpers (not used by the app) ──────────────────────────────────
/** @internal reset all module state so unit tests are deterministic. */
export function __resetDiagnosticsForTest(): void {
  ring.length = 0;
  sessionIds.length = 0;
  lastShipAt = 0;
  droppedShips = 0;
}
/** @internal snapshot the current ring. */
export function __ringSnapshotForTest(): DiagEntry[] {
  return ring.slice();
}
