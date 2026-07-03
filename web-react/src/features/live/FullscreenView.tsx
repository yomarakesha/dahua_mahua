import { useCallback, useEffect, useRef, useState } from "react";
import { MsePlayer, type PlayerStatus } from "@/components/video/MsePlayer";
import { streamName } from "@/api/types";
import { XIcon, VolumeOn, VolumeOff, ServerIcon, CameraIcon } from "@/components/icons";
import type { Camera } from "@/api/types";
import { fullscreenLayers, FADE_MS, TEAR_GRACE_MS } from "./fullscreen-upgrade";

interface Props {
  cam: Camera;
  onClose: () => void;
}

/**
 * Single-camera fullscreen overlay — SmartPSS-parity "instant view".
 *
 * Shows the SUB immediately (a warm producer opens it in ~0.5s), and mounts the
 * 4MP MAIN hidden behind it in parallel. When the main decodes its first frame
 * ("live"), it cross-fades over the sub and the sub socket is torn down. The 2s
 * main cold-open becomes invisible. Audio lives on the MAIN (the sub stays
 * muted). All the main-layer controls (engine, via-NVR, sound) act on the main.
 */
export function FullscreenView({ cam, onClose }: Props) {
  // Audio is OFF by default; the user enables it with the speaker button (a
  // user gesture, which browsers require to start audio). Only here in the
  // main/fullscreen view — grid tiles stay muted. Audio rides the MAIN layer.
  const [audioOn, setAudioOn] = useState(false);
  // Source for the MAIN stream: DIRECT from the camera IP by default. The NVR's
  // RTSP relay drops packets / times out on concurrent 4MP mains (measured 7815
  // lost vs 0 direct — exactly why the June-23 build was stable and why routing
  // mains via the NVR froze them). The toggle still offers Via-NVR (`_main_nvr`)
  // as a per-camera fallback when a camera isn't directly reachable.
  const [viaNvr, setViaNvr] = useState(false);

  // Transport ("engine") for the main. WebRTC is the DEFAULT via the vendor race
  // `mode="webrtc,mse"`: video-rtc.js starts BOTH transports and WebRTC wins if it
  // establishes (real-time, DROPS late frames → SmartPSS-like smoothness on the
  // 4MP main), else MSE auto-fills (buffered, plays every frame). Both carry AUDIO.
  // The engine toggle lets the operator force plain "mse" to A/B compare. The grid
  // is always MSE.
  const [mainStatus, setMainStatus] = useState<PlayerStatus>("connecting");
  // Engine toggle state: false → WebRTC-preferred ("webrtc,mse"), true → forced
  // plain MSE. Resets to WebRTC-preferred on every camera switch.
  const [forceMse, setForceMse] = useState(false);
  const mainMode: "webrtc,mse" | "mse" = forceMse ? "mse" : "webrtc,mse";

  const hasSub = cam.has_sub;
  const hasMain = cam.has_main;

  // Upgrade state machine: `mainLive` flips true the first time the main decodes
  // a frame; `subTorn` frees the sub socket once the cross-fade has finished.
  const [mainLive, setMainLive] = useState(false);
  const [subTorn, setSubTorn] = useState(false);

  const onMainStatus = useCallback((s: PlayerStatus) => {
    setMainStatus(s);
    if (s === "live") setMainLive(true);
  }, []);

  // Fresh connect state whenever the source or engine changes (the main remounts
  // on an engine switch via key={mainMode}; a via-NVR toggle re-points the same
  // player). Just resets the visible status to the spinner.
  useEffect(() => {
    setMainStatus("connecting");
  }, [viaNvr, forceMse, cam.id]);

  // Reset the sub→main upgrade whenever the camera changes (sidebar switch reuses
  // this instance) OR the engine is toggled (the main remounts and re-races, so
  // re-show the sub underneath and re-run the cross-fade when the new engine goes
  // live). Both layers tear down and re-open — no leaked sockets (each player's
  // unmount closes its ws + RTSP pull). Camera switch also drops back to WebRTC.
  useEffect(() => {
    setForceMse(false);
  }, [cam.id]);
  useEffect(() => {
    setMainLive(false);
    setSubTorn(false);
  }, [cam.id, forceMse]);

  // Cross-fade → tear the sub down. Once the main is live and has faded in over
  // the sub, free the sub socket (its pull is now wasted). Only when there's a
  // sub AND a main to upgrade to; a sub-only camera keeps its sub.
  useEffect(() => {
    if (!mainLive || !hasSub || !hasMain) return;
    const t = window.setTimeout(() => setSubTorn(true), FADE_MS + TEAR_GRACE_MS);
    return () => window.clearTimeout(t);
  }, [mainLive, hasSub, hasMain]);

  // Fallback: if the main DIES after we tore the sub down (post-upgrade error),
  // bring the sub back so the operator still sees the camera (via the sub) rather
  // than a bare "signal lost" overlay with nothing behind it. Resetting mainLive
  // makes the main layer transparent again; the main keeps self-healing beneath,
  // and when it returns to "live" the cross-fade re-runs. No loop: once subTorn is
  // false the guard fails, so it fires at most once per death.
  useEffect(() => {
    if (mainStatus === "error" && subTorn && hasSub) {
      setSubTorn(false);
      setMainLive(false);
    }
  }, [mainStatus, subTorn, hasSub]);

  const dialogRef = useRef<HTMLDivElement>(null);
  // Guard against the pointerdown-preconnect race: CameraTile opens fullscreen on
  // pointerdown (~150ms early), so the ensuing click can land on this backdrop and
  // instantly close it. Ignore backdrop closes for a short window after mount.
  const openedAtRef = useRef(Date.now());

  useEffect(() => {
    // Move initial focus into the dialog; restore to the opener on close.
    const opener = document.activeElement as HTMLElement | null;
    dialogRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      opener?.focus?.();
    };
  }, [onClose]);

  const layers = fullscreenLayers(hasSub, hasMain, mainLive, subTorn);
  const noStream = !hasSub && !hasMain;

  return (
    <div
      ref={dialogRef}
      role="dialog"
      aria-modal="true"
      aria-label={`${cam.display_name} — fullscreen live view`}
      tabIndex={-1}
      className="fixed inset-0 z-50 flex flex-col bg-black/95 backdrop-blur-sm focus:outline-none"
      onClick={() => {
        // Esc always works; a backdrop click closes only after the open-guard window.
        if (Date.now() - openedAtRef.current > 400) onClose();
      }}
    >
      <div className="flex flex-none items-center gap-3 px-6 py-4" onClick={(e) => e.stopPropagation()}>
        <span className="h-2 w-2 animate-pulse rounded-full bg-accent shadow-[0_0_8px_#2ecc71]" />
        <span className="text-base font-bold text-ink-bright">{cam.display_name}</span>
        <span className="font-mono text-2xs text-ink-faint">ch{cam.channel}</span>
        {hasMain && (
          <button
            type="button"
            onClick={() => setForceMse((v) => !v)}
            title={
              !forceMse
                ? "Engine: WebRTC — real-time, drops late frames (smooth, SmartPSS-like), auto-falls back to MSE. Click for buffered MSE."
                : "Engine: MSE — buffered, plays every frame in order. Click for smooth WebRTC."
            }
            className={[
              "rounded px-1.5 py-0.5 text-2xs font-semibold transition",
              !forceMse
                ? "bg-accent/[.12] text-accent-light hover:bg-accent/[.18]"
                : "bg-white/[.06] text-ink-dim hover:bg-white/[.1]",
            ].join(" ")}
          >
            {!forceMse ? "Engine: WebRTC (smooth)" : "Engine: MSE (buffered)"}
          </button>
        )}

        <div className="ml-auto flex items-center gap-2">
          {hasMain && (
            <button
              type="button"
              onClick={() => setViaNvr((v) => !v)}
              title={viaNvr ? "Source: via NVR — switch to direct camera" : "Source: direct camera — switch to via NVR"}
              className="flex h-9 items-center gap-2 rounded-lg border border-white/[.08] bg-white/[.04] px-3 text-sm font-semibold text-ink-mute transition hover:bg-white/[.08] hover:text-ink"
            >
              {viaNvr ? <ServerIcon size={15} /> : <CameraIcon size={15} />}
              {viaNvr ? "Via NVR" : "Direct"}
            </button>
          )}
          {hasMain && (
            <button
              type="button"
              onClick={() => setAudioOn((v) => !v)}
              title={audioOn ? "Mute" : "Enable sound"}
              className={[
                "flex h-9 items-center gap-2 rounded-lg border px-3 text-sm font-semibold transition",
                audioOn
                  ? "border-accent/30 bg-accent/[.12] text-accent-light"
                  : "border-white/[.08] bg-white/[.04] text-ink-mute hover:bg-white/[.08] hover:text-ink",
              ].join(" ")}
            >
              {audioOn ? <VolumeOn size={16} /> : <VolumeOff size={16} />}
              {audioOn ? "Sound on" : "Sound off"}
            </button>
          )}
          <button
            type="button"
            onClick={onClose}
            title="Close (Esc)"
            className="flex h-9 w-9 items-center justify-center rounded-lg border border-white/[.08] bg-white/[.04] text-ink-mute transition hover:bg-white/[.08] hover:text-ink"
          >
            <XIcon size={18} />
          </button>
        </div>
      </div>

      <div
        className="relative min-h-0 flex-1 px-6 pb-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="relative h-full w-full overflow-hidden rounded-xl border border-white/[.08] bg-black">
          {noStream ? (
            <div className="absolute inset-0 flex items-center justify-center">
              <span className="font-mono text-xs uppercase tracking-wider text-ink-faint">
                no stream available
              </span>
            </div>
          ) : (
            <>
              {/* SUB layer — instant, muted, sits underneath. Torn down once the
                  main has cross-faded in (subTorn). */}
              {layers.showSub && (
                <MsePlayer
                  key="sub"
                  src={streamName(cam, "sub")}
                  muted
                  mode="mse"
                  className="absolute inset-0 h-full w-full"
                />
              )}
              {/* MAIN layer — hi-res, upgrades in the background and cross-fades in
                  over the sub. Opacity toggles the fade; the layer is always mounted
                  (when hasMain) so it can warm up hidden. */}
              {layers.showMain && (
                <div
                  className="absolute inset-0 h-full w-full transition-opacity duration-300"
                  style={{ opacity: layers.mainOpaque ? 1 : 0 }}
                >
                  {/* 4MP main. mode="webrtc,mse" by default: the vendor races
                      WebRTC (drop-late → smooth) against MSE (buffered fallback) on
                      the SAME <video>; onStatus goes "live" when currentTime starts
                      advancing (either transport), which drives the cross-fade.
                      key={mainMode} remounts on an engine toggle so el.mode reapplies. */}
                  <MsePlayer
                    key={mainMode}
                    src={streamName(cam, "main", viaNvr)}
                    muted={!audioOn}
                    mode={mainMode}
                    onStatus={onMainStatus}
                    className="absolute inset-0 h-full w-full"
                  />
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
