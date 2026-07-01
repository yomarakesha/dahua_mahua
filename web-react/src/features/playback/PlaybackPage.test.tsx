import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "@/lib/auth";
import PlaybackPage from "./PlaybackPage";

// ── Fixtures ──────────────────────────────────────────────────────────────────

const MOCK_NVRS = [
  {
    id: "nvr1",
    label: "Office NVR",
    ip: "192.168.1.1",
    port: 554,
    rtsp_username: "admin",
    vendor: "dahua" as const,
    enabled: true,
    group: null,
    region_id: null,
    created_at: "",
    updated_at: "",
    camera_count: 2,
    create_notice: null,
  },
];

const MOCK_CAMERAS = [
  {
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
  },
  {
    id: "cam2",
    nvr_id: "nvr1",
    channel: 2,
    name: "Parking",
    ip: null,
    enabled: true,
    has_sub: true,
    has_main: true,
    display_name: "Parking",
    region_id: null,
  },
];

// ── Mocks ─────────────────────────────────────────────────────────────────────

vi.mock("@/api/hooks", () => ({
  useNvrs: () => ({ data: MOCK_NVRS, isLoading: false }),
  useCameras: () => ({ data: MOCK_CAMERAS, isLoading: false }),
  useRecordingAvailability: () => ({ data: null, isLoading: false }),
  useRecordingIndex: () => ({ data: null, isLoading: false }),
}));

// ── Helpers ───────────────────────────────────────────────────────────────────

function renderPage(initialEntries: string[] = ["/playback"]) {
  return render(
    <MemoryRouter initialEntries={initialEntries}>
      <AuthProvider>
        <PlaybackPage />
      </AuthProvider>
    </MemoryRouter>,
  );
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("PlaybackPage", () => {
  it("renders NVR selector with options", () => {
    renderPage();
    const nvrSelect = screen.getByRole("combobox", { name: /nvr/i });
    expect(nvrSelect).toBeTruthy();
    expect(screen.getByText("Office NVR")).toBeTruthy();
  });

  it("camera selector is disabled before NVR is selected", () => {
    renderPage();
    const camSelect = screen.getByRole("combobox", { name: /camera/i }) as HTMLSelectElement;
    expect(camSelect.disabled).toBe(true);
  });

  it("renders date input", () => {
    renderPage();
    const dateInput = screen.getByLabelText(/date/i);
    expect(dateInput).toBeTruthy();
  });

  it("renders all four speed buttons", () => {
    renderPage();
    for (const s of [1, 2, 4, 8]) {
      expect(screen.getByRole("button", { name: `${s}× speed` })).toBeTruthy();
    }
  });

  it("1× speed is active (aria-pressed=true) by default", () => {
    renderPage();
    const btn = screen.getByRole("button", { name: "1× speed" });
    expect(btn.getAttribute("aria-pressed")).toBe("true");
    // others should be false
    for (const s of [2, 4, 8]) {
      expect(
        screen.getByRole("button", { name: `${s}× speed` }).getAttribute("aria-pressed"),
      ).toBe("false");
    }
  });

  it("snapshot button is disabled until player ready", () => {
    renderPage();
    const snap = screen.getByRole("button", { name: /snapshot/i }) as HTMLButtonElement;
    expect(snap.disabled).toBe(true);
  });

  it("selecting an NVR enables camera selector and cameras appear", () => {
    renderPage();
    const nvrSelect = screen.getByRole("combobox", { name: /nvr/i }) as HTMLSelectElement;
    fireEvent.change(nvrSelect, { target: { value: "nvr1" } });

    const camSelect = screen.getByRole("combobox", { name: /camera/i }) as HTMLSelectElement;
    expect(camSelect.disabled).toBe(false);
    // Cameras for nvr1 appear in the select
    expect(screen.getByText(/Front Door ch1/)).toBeTruthy();
    expect(screen.getByText(/Parking ch2/)).toBeTruthy();
  });

  it("changing NVR resets camera selector value to empty", () => {
    renderPage();
    const nvrSelect = screen.getByRole("combobox", { name: /nvr/i }) as HTMLSelectElement;
    fireEvent.change(nvrSelect, { target: { value: "nvr1" } });

    const camSelect = screen.getByRole("combobox", { name: /camera/i }) as HTMLSelectElement;
    fireEvent.change(camSelect, { target: { value: "cam1" } });
    expect(camSelect.value).toBe("cam1");

    // Change NVR — camera should reset
    fireEvent.change(nvrSelect, { target: { value: "" } });
    expect(camSelect.value).toBe("");
  });

  it("clicking a speed button marks it as active", () => {
    renderPage();
    const btn4x = screen.getByRole("button", { name: "4× speed" });
    fireEvent.click(btn4x);
    expect(btn4x.getAttribute("aria-pressed")).toBe("true");
    expect(
      screen.getByRole("button", { name: "1× speed" }).getAttribute("aria-pressed"),
    ).toBe("false");
  });

  it("renders both transport toggle buttons, Smooth active by default", () => {
    renderPage();
    const smooth = screen.getByRole("button", { name: /smooth transport/i });
    const clear = screen.getByRole("button", { name: /clear transport/i });
    expect(smooth).toBeTruthy();
    expect(clear).toBeTruthy();
    expect(smooth.getAttribute("aria-pressed")).toBe("true");
    expect(clear.getAttribute("aria-pressed")).toBe("false");
  });

  it("clicking Clear activates it and deactivates Smooth", () => {
    renderPage();
    const smooth = screen.getByRole("button", { name: /smooth transport/i });
    const clear = screen.getByRole("button", { name: /clear transport/i });
    fireEvent.click(clear);
    expect(clear.getAttribute("aria-pressed")).toBe("true");
    expect(smooth.getAttribute("aria-pressed")).toBe("false");
  });

  it("shows the slow-loading caption only when Clear is active", () => {
    renderPage();
    expect(screen.queryByText(/loads slowly/i)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /clear transport/i }));
    expect(screen.getByText(/loads slowly/i)).toBeTruthy();
  });

  it("renders player placeholder area", () => {
    renderPage();
    expect(screen.getByTestId("player-placeholder")).toBeTruthy();
  });

  it("renders timeline placeholder area", () => {
    renderPage();
    expect(screen.getByTestId("timeline-placeholder")).toBeTruthy();
  });
});

// ── Deep-link preselect (Item 4: Live tile "Watch in Playback") ────────────────

describe("PlaybackPage — deep link (?nvr=&ch=)", () => {
  it("preselects NVR + camera from query params and defaults date to today", () => {
    renderPage(["/playback?nvr=nvr1&ch=2"]);

    const nvrSelect = screen.getByRole("combobox", { name: /nvr/i }) as HTMLSelectElement;
    expect(nvrSelect.value).toBe("nvr1");

    const camSelect = screen.getByRole("combobox", { name: /camera/i }) as HTMLSelectElement;
    expect(camSelect.value).toBe("cam2");

    const dateInput = screen.getByLabelText(/date/i) as HTMLInputElement;
    expect(dateInput.value).toBe(new Date().toISOString().slice(0, 10));
  });

  it("ignores an unknown deep-link camera and behaves like no params", () => {
    renderPage(["/playback?nvr=nvr1&ch=99"]);

    const nvrSelect = screen.getByRole("combobox", { name: /nvr/i }) as HTMLSelectElement;
    expect(nvrSelect.value).toBe("");
    const camSelect = screen.getByRole("combobox", { name: /camera/i }) as HTMLSelectElement;
    expect(camSelect.disabled).toBe(true);
  });

  it("behaves exactly as today when no deep-link params are present", () => {
    renderPage(["/playback"]);
    const nvrSelect = screen.getByRole("combobox", { name: /nvr/i }) as HTMLSelectElement;
    expect(nvrSelect.value).toBe("");
  });
});
