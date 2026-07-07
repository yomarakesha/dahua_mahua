/**
 * FullscreenView engine-selection tests — the 4MP MAIN now streams via go2rtc
 * WebRTC (drop-late → SmartPSS-like smoothness) with automatic MSE fallback.
 *
 * The MAIN must mount with mode="webrtc" by default (WebRTC only; app-level MSE fallback on failure
 * against MSE on the same <video>), and mode="mse" when the operator forces the
 * MSE engine via the toggle. The SUB layer stays plain MSE.
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { FullscreenView } from "./FullscreenView";
import { streamName, type Camera } from "@/api/types";
import i18n from "@/i18n";

// The `live` namespace is merged into en.json separately; register the engine
// labels this test asserts so it stays green whether or not that merge ran.
i18n.addResourceBundle(
  "en",
  "translation",
  {
    live: {
      engineWebrtc: "Engine: WebRTC (smooth)",
      engineMse: "Engine: MSE (buffered)",
    },
  },
  true,
  true,
);

// Stub the real player (it opens a live WebSocket / RTSP pull). Each stub exposes
// its `src` + `mode` so we can assert which transport each layer mounted with.
vi.mock("@/components/video/MsePlayer", () => ({
  MsePlayer: ({ src, mode }: { src: string; mode?: string }) => (
    <div data-testid="mse-player" data-src={src} data-mode={mode ?? "mse"} />
  ),
}));

const CAM: Camera = {
  id: "cam1",
  nvr_id: "nvr-abc",
  channel: 3,
  name: "Front Door",
  ip: null,
  enabled: true,
  has_sub: true,
  has_main: true,
  display_name: "Front Door",
  region_id: null,
};

const mainSrc = streamName(CAM, "main");
const subSrc = streamName(CAM, "sub");

function mainLayer() {
  return screen
    .getAllByTestId("mse-player")
    .find((el) => el.getAttribute("data-src") === mainSrc)!;
}
function subLayer() {
  return screen
    .getAllByTestId("mse-player")
    .find((el) => el.getAttribute("data-src") === subSrc)!;
}

describe("FullscreenView — WebRTC main with MSE fallback", () => {
  it("mounts the MAIN with mode='webrtc' by default (WebRTC preferred)", () => {
    render(<FullscreenView cam={CAM} onClose={vi.fn()} />);
    expect(mainLayer().getAttribute("data-mode")).toBe("webrtc");
  });

  it("keeps the SUB layer on plain MSE", () => {
    render(<FullscreenView cam={CAM} onClose={vi.fn()} />);
    expect(subLayer().getAttribute("data-mode")).toBe("mse");
  });

  it("defaults the engine toggle to WebRTC (smooth)", () => {
    render(<FullscreenView cam={CAM} onClose={vi.fn()} />);
    expect(screen.getByRole("button", { name: /engine: webrtc \(smooth\)/i })).toBeTruthy();
  });

  it("forcing the MSE engine mounts the MAIN with mode='mse'", () => {
    render(<FullscreenView cam={CAM} onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /engine: webrtc \(smooth\)/i }));
    expect(screen.getByRole("button", { name: /engine: mse \(buffered\)/i })).toBeTruthy();
    expect(mainLayer().getAttribute("data-mode")).toBe("mse");
  });

  it("toggling back returns the MAIN to mode='webrtc'", () => {
    render(<FullscreenView cam={CAM} onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /engine: webrtc/i }));
    fireEvent.click(screen.getByRole("button", { name: /engine: mse/i }));
    expect(mainLayer().getAttribute("data-mode")).toBe("webrtc");
  });

  it("Escape closes the view (a11y preserved)", () => {
    const onClose = vi.fn();
    render(<FullscreenView cam={CAM} onClose={onClose} />);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });
});
