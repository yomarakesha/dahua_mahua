import { memo, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { MsePlayer, type PlayerStatus } from "@/components/video/MsePlayer";
import { FilmIcon } from "@/components/icons";
import { streamName } from "@/api/types";
import type { Camera } from "@/api/types";

const SHADOW = "0 1px 3px #000";

interface Props {
  cam: Camera;
  onOpen: (cam: Camera) => void;
}

/**
 * One live camera cell. Plays the SUB stream (low-res, grid-friendly), falls
 * back to MAIN when the camera has no sub, and shows a muted placeholder when
 * neither stream exists. Click opens the fullscreen (main) view.
 *
 * Memoized + no ticking time prop: the camera burns its own timestamp into the
 * video, so we don't re-render every tile once per second just for an overlay.
 */
export const CameraTile = memo(function CameraTile({ cam, onOpen }: Props) {
  const quality = cam.has_sub ? "sub" : cam.has_main ? "main" : null;
  const [status, setStatus] = useState<PlayerStatus>("connecting");
  const [menuPos, setMenuPos] = useState<{ x: number; y: number } | null>(null);
  const navigate = useNavigate();

  // Close the context menu on any outside click or Escape.
  useEffect(() => {
    if (!menuPos) return;
    const close = () => setMenuPos(null);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    window.addEventListener("click", close);
    window.addEventListener("contextmenu", close);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("click", close);
      window.removeEventListener("contextmenu", close);
      window.removeEventListener("keydown", onKey);
    };
  }, [menuPos]);

  function handleWatchInPlayback() {
    setMenuPos(null);
    navigate(`/playback?nvr=${encodeURIComponent(cam.nvr_id)}&ch=${cam.channel}`);
  }

  return (
    <>
      <button
        type="button"
        onClick={() => onOpen(cam)}
        onContextMenu={(e) => {
          e.preventDefault();
          setMenuPos({ x: e.clientX, y: e.clientY });
        }}
        className="group relative overflow-hidden rounded border border-white/[.06] bg-black text-left focus:outline-none focus-visible:ring-1 focus-visible:ring-accent/40"
      >
        {quality ? (
          <MsePlayer
            src={streamName(cam, quality)}
            onStatus={setStatus}
            className="absolute inset-0 h-full w-full"
          />
        ) : (
          <div className="absolute inset-0 flex items-center justify-center bg-deep">
            <span className="font-mono text-3xs uppercase tracking-wider text-ink-faint">
              no stream
            </span>
          </div>
        )}

        {/* legibility gradient */}
        <div className="pointer-events-none absolute inset-0 bg-gradient-to-b from-black/35 via-transparent to-black/55" />

        {quality && status === "live" && (
          <div className="pointer-events-none absolute left-1.5 top-1.5 flex items-center gap-1">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent shadow-[0_0_6px_#2ecc71]" />
            <span
              className="text-3xs font-extrabold tracking-wide text-[#d8efe2]"
              style={{ textShadow: SHADOW }}
            >
              LIVE
            </span>
          </div>
        )}

        <div
          className="pointer-events-none absolute bottom-1.5 left-1.5 max-w-[90%] truncate text-3xs font-bold text-[#eef4f0]"
          style={{ textShadow: SHADOW }}
        >
          {cam.display_name}
        </div>
      </button>

      {menuPos && (
        <div
          role="menu"
          aria-label={`${cam.display_name} actions`}
          className="fixed z-50 min-w-[180px] overflow-hidden rounded-md border border-white/[.08] bg-[#161b22] py-1 text-sm text-ink-soft shadow-lg"
          style={{ top: menuPos.y, left: menuPos.x }}
          onClick={(e) => e.stopPropagation()}
        >
          <button
            type="button"
            role="menuitem"
            onClick={handleWatchInPlayback}
            className="flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-white/[.06]"
          >
            <FilmIcon size={14} />
            Watch in Playback
          </button>
        </div>
      )}
    </>
  );
});
