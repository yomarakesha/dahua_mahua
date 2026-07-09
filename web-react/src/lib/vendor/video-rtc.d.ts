// Minimal typings for the vendored go2rtc player (video-rtc.js, MIT).
export class VideoRTC extends HTMLElement {
  mode: string;
  background: boolean;
  src: string | URL;
  video: HTMLVideoElement;
  /** Underlying signalling WebSocket (null when disconnected). Read-only here —
   *  used to detect the "socket OPEN but data stopped" stall (self-heal). */
  ws: WebSocket | null;
  oninit(): void;
  /** Closes the WebSocket + RTCPeerConnection and clears the <video>. */
  ondisconnect(): void;
}
