/**
 * Constants & configuration.
 */

export const CONFIG = {
  // Backend FastAPI — all inventory + stream-URL handout goes through here.
  // Browser never talks to MediaMTX's :9997 control API anymore; that's
  // backend-only and shouldn't be exposed externally.
  backendBase:    `${location.protocol}//${location.hostname}:8000/api/v1`,
  // Browser DOES talk to MediaMTX directly for media (WHEP/HLS) — proxying
  // those through the backend would defeat the fan-out.
  webrtcBase:     `${location.protocol}//${location.hostname}:8889`,
  hlsBase:        `${location.protocol}//${location.hostname}:8888`,
  // Delivery relay: "go2rtc-mse" (buffered MSE — absorbs camera frame-timing
  // jitter that freezes WebRTC at 0% loss) or "mediamtx" (legacy WebRTC/WHEP).
  relay:          "go2rtc-mse",
  go2rtcWsBase:   `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.hostname}:1984`,
  // ICE servers for the browser-side RTCPeerConnection. EMPTY on purpose:
  // DSS is a LAN fan-out, so the browser and MediaMTX exchange host
  // candidates directly and connect instantly. A public STUN server here is
  // pure overhead — ICE would block waiting for a server-reflexive candidate
  // (and stall for the full timeout when the box has no internet), delaying
  // first frame. Only add a STUN/TURN entry if operators watch from OUTSIDE
  // the LAN, e.g. [{ urls: "stun:stun.l.google.com:19302" }].
  iceServers:     [],
  pollInterval:   10000,
  maxConcurrent:  8,
  reconnectBase:  2000,
  reconnectMax:   30000,
  gridPresets:    [2, 4, 8, 16, 32, 64],
  patrolIntervals:[5, 10, 15, 30, 60],
};

export const LS = {
  groups:  "dss_groups",
  layouts: "dss_layouts",
  prefs:   "dss_prefs",
};

export const STALL_CHECK_INTERVAL = 4000;
export const STALL_THRESHOLD = 8;

// Receiver-side jitter buffer target (ms). WebRTC's default buffer is tiny
// (~30–150ms) and freezes at 0% loss when the source delivers frames with
// irregular timing (camera on a jittery LAN). Enlarging it trades a little
// latency for far fewer freezes — applied per video receiver via
// jitterBufferTarget (Firefox) / playoutDelayHint (Chrome). 0 = leave default.
export const JITTER_BUFFER_MS = 800;

// How long to wait for a transient ICE `disconnected` to self-recover before
// treating it as an error and reconnecting. `disconnected` (unlike `failed`)
// routinely heals on its own when the main thread is briefly starved; an
// immediate reconnect tears down a connection that would have recovered and
// re-opens the NVR RTSP source, snowballing into a reconnect storm.
export const ICE_DISCONNECT_GRACE = 4000;
