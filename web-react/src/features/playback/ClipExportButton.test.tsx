import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import ClipExportButton from "./ClipExportButton";
import { buildClipUrl } from "./playback-utils";
import { setToken } from "@/api/client";

const SEL = { start: 1_751_241_600, end: 1_751_241_900 };

function renderBtn() {
  const onClear = vi.fn();
  render(
    <ClipExportButton nvrId="nvr1" channel={2} selection={SEL} onClear={onClear} />,
  );
  return { onClear };
}

describe("buildClipUrl", () => {
  it("encodes token, floors start/end, and targets the clip endpoint", () => {
    const url = buildClipUrl("http://x/api/v1", "nvr1", 2, 100.9, 400.2, "a b/c");
    expect(url).toBe(
      "http://x/api/v1/playback/nvr1/2/clip?start=100&end=400&token=a%20b%2Fc",
    );
  });
});

describe("ClipExportButton", () => {
  beforeEach(() => setToken("test-token"));
  afterEach(() => {
    setToken(null);
    vi.restoreAllMocks();
  });

  it("shows the slow-pull warning and an Export clip button", () => {
    renderBtn();
    expect(screen.getByRole("button", { name: /export clip/i })).toBeTruthy();
    expect(screen.getByText(/several minutes/i)).toBeTruthy();
  });

  it("gates on the in-flight request: disables + shows preparing state", async () => {
    // fetch that never resolves → stays in "preparing".
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
    renderBtn();
    const btn = screen.getByRole("button", { name: /export clip/i }) as HTMLButtonElement;
    fireEvent.click(btn);
    await waitFor(() => expect(screen.getByText(/preparing clip/i)).toBeTruthy());
    expect(btn.disabled).toBe(true);
  });

  it("surfaces 'recorder busy' on a 429 response", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ status: 429, ok: false }) as Response));
    renderBtn();
    fireEvent.click(screen.getByRole("button", { name: /export clip/i }));
    await waitFor(() => expect(screen.getByText(/recorder busy/i)).toBeTruthy());
  });
});
