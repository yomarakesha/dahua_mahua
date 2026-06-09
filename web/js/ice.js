/**
 * Pure decision logic for WebRTC ICE connection-state transitions.
 *
 * `disconnected` is deliberately NOT treated as a failure: it is transient and
 * usually self-recovers, so we arm a grace timer instead of reconnecting at
 * once. Only `failed`/`closed` (or the grace timer firing) trigger a reconnect.
 *
 * @param {string} iceState - RTCPeerConnection.iceConnectionState
 * @param {boolean} gracePending - whether a disconnect grace timer is armed
 * @returns {"reconnect"|"start-grace"|"cancel-grace"|"ignore"}
 */
export function decideIceAction(iceState, gracePending) {
  if (iceState === "failed" || iceState === "closed") return "reconnect";
  if (iceState === "disconnected") return gracePending ? "ignore" : "start-grace";
  if (iceState === "connected" || iceState === "completed") return "cancel-grace";
  return "ignore";
}
