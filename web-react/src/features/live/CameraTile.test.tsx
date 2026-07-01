/**
 * CameraTile context-menu tests — Item 4 (UX fix):
 * right-clicking a live tile shows a "Watch in Playback" action that
 * navigates to /playback with the camera's NVR + channel as query params.
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter, Routes, Route, useLocation } from "react-router-dom";
import { CameraTile } from "./CameraTile";
import type { Camera } from "@/api/types";

// The <dss-mse> custom element pulls a live WebSocket in real usage — stub the
// player entirely so these tests only exercise the tile's own UI.
vi.mock("@/components/video/MsePlayer", () => ({
  MsePlayer: () => <div data-testid="mse-player-stub" />,
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

function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="playback-location">{loc.pathname + loc.search}</div>;
}

function renderTile(onOpen = vi.fn()) {
  render(
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route path="/" element={<CameraTile cam={CAM} onOpen={onOpen} />} />
        <Route path="/playback" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  );
  return { onOpen };
}

describe("CameraTile — right-click context menu", () => {
  it("does not show the menu before a right-click", () => {
    renderTile();
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("right-click opens a menu with 'Watch in Playback'", () => {
    renderTile();
    fireEvent.contextMenu(screen.getByRole("button"));
    expect(screen.getByRole("menuitem", { name: /watch in playback/i })).toBeTruthy();
  });

  it("left-click still opens the tile fullscreen (unaffected)", () => {
    const { onOpen } = renderTile();
    fireEvent.click(screen.getByRole("button"));
    expect(onOpen).toHaveBeenCalledWith(CAM);
  });

  it("clicking 'Watch in Playback' navigates with nvr + ch query params", () => {
    renderTile();
    fireEvent.contextMenu(screen.getByRole("button"));
    fireEvent.click(screen.getByRole("menuitem", { name: /watch in playback/i }));
    expect(screen.getByTestId("playback-location").textContent).toBe(
      "/playback?nvr=nvr-abc&ch=3",
    );
  });

  it("Escape closes the menu", () => {
    renderTile();
    fireEvent.contextMenu(screen.getByRole("button"));
    expect(screen.getByRole("menu")).toBeTruthy();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
  });
});
