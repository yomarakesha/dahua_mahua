/**
 * LiveSidebar interaction tests — Item 1 (UX fix):
 *   - Single click on an NVR row selects it (shows its cameras in the grid).
 *   - Double click on an NVR row selects it AND expands the tree node.
 *   - The chevron remains an independent expand/collapse click target.
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { LiveSidebar } from "./LiveSidebar";
import type { Camera, Nvr } from "@/api/types";

const NVR: Nvr = {
  id: "nvr1",
  label: "testik",
  ip: "10.10.1.1",
  port: 37777,
  rtsp_username: "admin",
  vendor: "dahua",
  enabled: true,
  group: null,
  region_id: null,
  created_at: "",
  updated_at: "",
  camera_count: 1,
  create_notice: null,
};

const CAM: Camera = {
  id: "cam1",
  nvr_id: "nvr1",
  channel: 1,
  name: "Front Door",
  ip: null,
  enabled: true,
  has_sub: true,
  has_main: true,
  display_name: "Front Door",
  region_id: null,
};

function renderSidebar(overrides: Partial<Parameters<typeof LiveSidebar>[0]> = {}) {
  const onSelectNvr = vi.fn();
  const onPickCamera = vi.fn();
  render(
    <LiveSidebar
      nvrs={[NVR]}
      cameras={[CAM]}
      countByNvr={{ nvr1: 1 }}
      healthyById={{ nvr1: true }}
      selectedNvrId={null}
      onSelectNvr={onSelectNvr}
      onPickCamera={onPickCamera}
      visibleStreams={1}
      load={0.25}
      {...overrides}
    />,
  );
  return { onSelectNvr, onPickCamera };
}

describe("LiveSidebar — NVR row click behavior", () => {
  it("single click on the row selects the NVR (shows its cameras)", () => {
    const { onSelectNvr } = renderSidebar();
    fireEvent.click(screen.getByText("testik"));
    expect(onSelectNvr).toHaveBeenCalledWith("nvr1");
    // The camera tree is not expanded by a single click.
    expect(screen.queryByText("Front Door")).toBeNull();
  });

  it("double click on the row selects the NVR AND expands the tree", () => {
    const { onSelectNvr } = renderSidebar();
    fireEvent.doubleClick(screen.getByText("testik"));
    expect(onSelectNvr).toHaveBeenCalledWith("nvr1");
    expect(screen.getByText("Front Door")).toBeTruthy();
  });

  it("chevron click expands/collapses independently of selection", () => {
    const { onSelectNvr } = renderSidebar();
    fireEvent.click(screen.getByRole("button", { name: "Expand" }));
    expect(screen.getByText("Front Door")).toBeTruthy();
    // Selection callback should not fire just from expanding.
    expect(onSelectNvr).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Collapse" }));
    expect(screen.queryByText("Front Door")).toBeNull();
  });

  it("clicking a camera in the expanded tree opens it", () => {
    const { onPickCamera } = renderSidebar();
    fireEvent.doubleClick(screen.getByText("testik"));
    fireEvent.click(screen.getByText("Front Door"));
    expect(onPickCamera).toHaveBeenCalledWith(CAM);
  });
});
