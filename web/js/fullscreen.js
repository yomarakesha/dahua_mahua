/**
 * Fullscreen view: HD (main stream) ↔ SD (sub stream) toggle,
 * with WebRTC primary and HLS fallback. Buffers main stream in a hidden
 * <video> and swaps it in only when frames arrive — sub stream stays visible
 * during the transition (never shows black).
 */

import { CONFIG } from "./config.js";
import { state } from "./state.js";
import { dom } from "./dom.js";
import { labelFor, showToast } from "./utils.js";
import { resetVideoElement } from "./streams.js";

export function openFullscreen(path) {
  const conn = state.connections[path];
  const token = ++state.fullscreenToken;
  state.fullscreenPath = path;
  state.fullscreenIsMain = true;
  dom.fsTitle.textContent = labelFor(path);
  resetVideoElement(dom.fsVideo);
  // Always (re)open muted — audio never carries over from a previous camera;
  // the operator opts in per session by clicking the sound button.
  dom.fsVideo.muted = true;
  updateSoundBtn();

  // Show sub-stream immediately so user never sees black
  if (conn && conn.video && conn.video.srcObject) {
    dom.fsVideo.srcObject = conn.video.srcObject;
    dom.fsVideo.play().catch(() => {});
  } else if (conn && conn.video && conn.video.src) {
    dom.fsVideo.src = conn.video.src;
    dom.fsVideo.load();
    dom.fsVideo.play().catch(() => {});
  }

  dom.fsOverlay.classList.remove("hidden");
  dom.fsOverlay.requestFullscreen().catch(() => {});
  updateQualityBtn();

  connectFullscreenMain(path, token);
}

export function updateQualityBtn() {
  dom.fsQualityBtn.textContent = state.fullscreenIsMain ? "HD" : "SD";
  dom.fsQualityBtn.title = state.fullscreenIsMain
    ? "Viewing main stream (press Q for sub)"
    : "Viewing sub stream (press Q for main)";
  dom.fsQualityBtn.classList.toggle("active", state.fullscreenIsMain);
}

// Mute/unmute the visible video. The hidden buffer stays muted always, so the
// swap during HD↔SD transitions never double-plays audio.
export function toggleFullscreenSound() {
  if (!state.fullscreenPath) return;
  const wasUnmuting = dom.fsVideo.muted;
  dom.fsVideo.muted = !dom.fsVideo.muted;
  // Unmuting happens inside a click/keypress handler, which is the user gesture
  // the browser's autoplay-with-sound policy requires — re-issue play() so it
  // takes effect immediately.
  dom.fsVideo.play().catch(() => {});
  updateSoundBtn();
  // If we just unmuted a WebRTC main stream that carries no audio track, the
  // button would appear to "do nothing" — say why instead of staying silent.
  // Only check once the main stream has actually swapped in: before the swap
  // (and on the SD sub-stream) the visible video is video-only by design, so a
  // "no audio" warning there would be a false alarm. Also skip the HLS fallback
  // (srcObject is null but the <video> src may still carry AAC the browser plays).
  const swappedMain = state.fullscreenConn && state.fullscreenConn.swapped;
  if (wasUnmuting && swappedMain && dom.fsVideo.srcObject) {
    const audioTracks = dom.fsVideo.srcObject.getAudioTracks
      ? dom.fsVideo.srcObject.getAudioTracks() : [];
    if (audioTracks.length === 0) {
      showToast(
        "В этом потоке нет звука — камера не передаёт аудио, или кодек AAC " +
        "(WebRTC его не переносит). Включите аудио на канале в кодеке G.711.",
        "warning", 5000,
      );
    }
  }
}

function updateSoundBtn() {
  const on = !dom.fsVideo.muted;
  dom.fsSoundBtn.textContent = on ? "🔊" : "🔇";
  dom.fsSoundBtn.title = on ? "Mute audio (M)" : "Unmute audio (M)";
  dom.fsSoundBtn.classList.toggle("active", on);
}

export function toggleFullscreenQuality() {
  if (!state.fullscreenPath) return;
  state.fullscreenIsMain = !state.fullscreenIsMain;
  updateQualityBtn();
  const token = ++state.fullscreenToken;
  disconnectFullscreenMain();

  if (state.fullscreenIsMain) {
    connectFullscreenMain(state.fullscreenPath, token);
  } else {
    const conn = state.connections[state.fullscreenPath];
    resetVideoElement(dom.fsVideo);
    if (conn && conn.stream) {
      dom.fsVideo.srcObject = conn.stream;
      dom.fsVideo.play().catch(() => {});
    } else if (conn && conn.hlsUrl) {
      dom.fsVideo.src = conn.hlsUrl;
      dom.fsVideo.load();
      dom.fsVideo.play().catch(() => {});
    }
  }
}

async function connectFullscreenMain(path, token) {
  disconnectFullscreenMain();
  const mainPath = path + "_main";

  let pc;
  try {
    pc = new RTCPeerConnection({
      iceServers: [{ urls: "stun:stun.l.google.com:19302" }],
    });
  } catch (_) {
    tryFullscreenHLS(mainPath, path, token);
    return;
  }
  state.fullscreenConn = { pc, swapped: false, token, _swapTimer: null };

  pc.addTransceiver("video", { direction: "recvonly" });
  // Also request audio. If the camera emits a WebRTC-compatible track (G.711
  // PCMA/PCMU) it arrives in the same MediaStream and plays once unmuted; if
  // the source has no audio (or AAC, which WebRTC can't carry) this transceiver
  // just stays inactive — video is unaffected.
  pc.addTransceiver("audio", { direction: "recvonly" });

  pc.ontrack = (evt) => {
    if (!state.fullscreenConn || state.fullscreenConn.pc !== pc || state.fullscreenConn.token !== token) return;
    dom.fsBuffer.srcObject = evt.streams[0];

    const onReady = () => {
      if (!state.fullscreenConn || state.fullscreenConn.pc !== pc || state.fullscreenConn.token !== token) return;
      if (dom.fsBuffer.videoWidth > 0) {
        dom.fsVideo.srcObject = dom.fsBuffer.srcObject;
        dom.fsVideo.play().catch(() => {});
        dom.fsBuffer.srcObject = null;
        state.fullscreenConn.swapped = true;
      }
    };
    dom.fsBuffer.addEventListener("playing", onReady, { once: true });
    state.fullscreenConn._swapTimer = setTimeout(onReady, 3000);
  };

  pc.oniceconnectionstatechange = () => {
    if (!state.fullscreenConn || state.fullscreenConn.pc !== pc || state.fullscreenConn.token !== token) return;
    const s = pc.iceConnectionState;
    if (s === "failed" || s === "disconnected" || s === "closed") {
      if (!state.fullscreenConn.swapped) {
        try { pc.close(); } catch(_){}
        tryFullscreenHLS(mainPath, path, token);
      }
    }
  };

  try {
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    const abort = new AbortController();
    const timer = setTimeout(() => abort.abort(), 15000);
    const res = await fetch(`${CONFIG.webrtcBase}/${mainPath}/whep`, {
      method: "POST",
      headers: { "Content-Type": "application/sdp" },
      body: pc.localDescription.sdp,
      signal: abort.signal,
    });
    clearTimeout(timer);
    if (!res.ok) throw new Error(`WHEP ${res.status}`);
    const answer = await res.text();
    await pc.setRemoteDescription({ type: "answer", sdp: answer });
  } catch (_) {
    try { pc.close(); } catch(_e){}
    if (state.fullscreenConn && !state.fullscreenConn.swapped && state.fullscreenConn.token === token) {
      tryFullscreenHLS(mainPath, path, token);
    }
  }
}

function tryFullscreenHLS(mainPath, subPath, token) {
  const hlsUrl = `${CONFIG.hlsBase}/${mainPath}/index.m3u8`;
  dom.fsBuffer.srcObject = null;
  dom.fsBuffer.src = hlsUrl;
  dom.fsBuffer.load();
  dom.fsBuffer.play().catch(() => {});

  let resolved = false;
  const onReady = () => {
    if (resolved || !state.fullscreenPath || state.fullscreenToken !== token) return;
    resolved = true;
    if (dom.fsBuffer.videoWidth > 0) {
      dom.fsVideo.srcObject = null;
      dom.fsVideo.src = hlsUrl;
      dom.fsVideo.load();
      dom.fsVideo.play().catch(() => {});
      dom.fsBuffer.removeAttribute("src");
      dom.fsBuffer.load();
    }
  };
  dom.fsBuffer.addEventListener("playing", onReady, { once: true });

  setTimeout(() => {
    if (resolved || !state.fullscreenPath || state.fullscreenToken !== token) return;
    if (dom.fsBuffer.readyState < 2) {
      state.fullscreenIsMain = false;
      updateQualityBtn();
      showToast("Main stream unavailable — showing sub-stream", "warning", 4000);
      resetVideoElement(dom.fsBuffer);
    }
  }, 8000);
}

function disconnectFullscreenMain() {
  if (state.fullscreenConn) {
    if (state.fullscreenConn._swapTimer) clearTimeout(state.fullscreenConn._swapTimer);
    if (state.fullscreenConn.pc) {
      try { state.fullscreenConn.pc.close(); } catch (_) {}
    }
    state.fullscreenConn = null;
  }
  resetVideoElement(dom.fsBuffer);
}

export function closeFullscreen() {
  state.fullscreenToken++;
  disconnectFullscreenMain();
  dom.fsOverlay.classList.add("hidden");
  resetVideoElement(dom.fsVideo);
  state.fullscreenPath = null;
  state.fullscreenIsMain = true;
  if (document.fullscreenElement) {
    document.exitFullscreen().catch(() => {});
  }
}

// Handle browser-initiated fullscreen exit (e.g. native Esc)
document.addEventListener("fullscreenchange", () => {
  if (!document.fullscreenElement && state.fullscreenPath) {
    state.fullscreenToken++;
    disconnectFullscreenMain();
    dom.fsOverlay.classList.add("hidden");
    resetVideoElement(dom.fsVideo);
    state.fullscreenPath = null;
    state.fullscreenIsMain = true;
  }
});
