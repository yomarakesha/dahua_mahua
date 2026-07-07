import { useCallback, useEffect, useRef, useState } from "react";
import type React from "react";

/**
 * Digital (client-side) zoom for the playback video. Purely visual — a CSS
 * `transform: translate() scale()` on the <video>; it never touches MSE/decoding
 * (so zooming a 4MP main just magnifies the already-decoded frame).
 *
 * Usage: spread `containerProps` on the video's positioned wrapper (it gets the
 * ref + pointer handlers + a grab cursor when zoomed; a non-passive wheel
 * listener is attached to the ref so `preventDefault` works), and pass
 * `videoStyle` to the player's `videoStyle` prop. `zoomed`/`scale`/`reset` drive
 * a small reset control.
 */
const MIN_SCALE = 1;
const MAX_SCALE = 4;

export interface VideoZoom {
  videoStyle: React.CSSProperties;
  containerProps: {
    ref: React.RefObject<HTMLDivElement>;
    onPointerDown: (e: React.PointerEvent) => void;
    onPointerMove: (e: React.PointerEvent) => void;
    onPointerUp: (e: React.PointerEvent) => void;
    onPointerLeave: (e: React.PointerEvent) => void;
    style?: React.CSSProperties;
  };
  zoomed: boolean;
  scale: number;
  reset: () => void;
}

export function useVideoZoom(): VideoZoom {
  const boxRef = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(1);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const drag = useRef<{ x: number; y: number; ox: number; oy: number } | null>(null);
  const [dragging, setDragging] = useState(false);

  // Keep the panned image inside the frame: at scale s the image overflows the
  // box by (dim*(s-1)); clamp the translate to half of that on each axis.
  const clamp = useCallback((x: number, y: number, s: number) => {
    const el = boxRef.current;
    if (!el || s <= 1) return { x: 0, y: 0 };
    const maxX = (el.clientWidth * (s - 1)) / 2;
    const maxY = (el.clientHeight * (s - 1)) / 2;
    return { x: Math.max(-maxX, Math.min(maxX, x)), y: Math.max(-maxY, Math.min(maxY, y)) };
  }, []);

  const reset = useCallback(() => {
    setScale(1);
    setOffset({ x: 0, y: 0 });
  }, []);

  // Non-passive wheel so preventDefault stops the page from scrolling while zooming.
  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      setScale((s) => {
        const next = Math.min(MAX_SCALE, Math.max(MIN_SCALE, s * (e.deltaY < 0 ? 1.15 : 0.87)));
        setOffset((o) => (next <= 1 ? { x: 0, y: 0 } : clamp(o.x, o.y, next)));
        return Number(next.toFixed(3));
      });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [clamp]);

  const onPointerDown = useCallback(
    (e: React.PointerEvent) => {
      if (scale <= 1) return; // only pan when zoomed in
      drag.current = { x: e.clientX, y: e.clientY, ox: offset.x, oy: offset.y };
      setDragging(true);
      (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
    },
    [scale, offset],
  );

  const onPointerMove = useCallback(
    (e: React.PointerEvent) => {
      const d = drag.current;
      if (!d) return;
      setOffset(clamp(d.ox + (e.clientX - d.x), d.oy + (e.clientY - d.y), scale));
    },
    [clamp, scale],
  );

  const endDrag = useCallback(() => {
    drag.current = null;
    setDragging(false);
  }, []);

  const videoStyle: React.CSSProperties = {
    transform: `translate(${offset.x}px, ${offset.y}px) scale(${scale})`,
    transformOrigin: "center center",
    transition: dragging ? "none" : "transform 0.08s ease-out",
    willChange: scale > 1 ? "transform" : undefined,
  };

  return {
    videoStyle,
    containerProps: {
      ref: boxRef,
      onPointerDown,
      onPointerMove,
      onPointerUp: endDrag,
      onPointerLeave: endDrag,
      style: scale > 1 ? { cursor: dragging ? "grabbing" : "grab" } : undefined,
    },
    zoomed: scale > 1,
    scale,
    reset,
  };
}
