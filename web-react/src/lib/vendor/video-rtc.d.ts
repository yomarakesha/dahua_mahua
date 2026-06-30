// Minimal typings for the vendored go2rtc player (video-rtc.js, MIT).
export class VideoRTC extends HTMLElement {
  mode: string;
  background: boolean;
  src: string | URL;
  video: HTMLVideoElement;
  oninit(): void;
  /** Closes the WebSocket + RTCPeerConnection and clears the <video>. */
  ondisconnect(): void;
}
