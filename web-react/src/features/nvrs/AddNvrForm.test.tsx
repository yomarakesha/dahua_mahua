/**
 * AddNvrForm tests — vendor UX fix:
 *   - The Vendor selector is visible in the main form (NOT hidden under Advanced).
 *   - Submitting includes the chosen vendor in the create payload.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

const state = vi.hoisted(() => ({ mutate: vi.fn() }));

vi.mock("@/api/hooks", () => ({
  useCreateNvr: () => ({
    mutate: state.mutate,
    isPending: false,
    isError: false,
    isSuccess: false,
    error: null,
    data: null,
  }),
}));

import { AddNvrForm } from "./AddNvrForm";

beforeEach(() => {
  state.mutate.mockReset();
});

describe("AddNvrForm — vendor selector", () => {
  it("shows the Vendor selector in the main form without opening Advanced", () => {
    render(<AddNvrForm />);
    // Present immediately — no need to click the Advanced disclosure.
    const vendor = screen.getByLabelText("Vendor") as HTMLSelectElement;
    expect(vendor).toBeTruthy();
    expect(vendor.value).toBe("dahua");
    expect(screen.getByRole("option", { name: "hikvision" })).toBeTruthy();
  });

  it("submits the chosen vendor in the create payload", () => {
    render(<AddNvrForm />);
    fireEvent.change(screen.getByPlaceholderText("Lobby NVR"), {
      target: { value: "Front NVR" },
    });
    fireEvent.change(screen.getByPlaceholderText("192.168.1.10"), {
      target: { value: "192.168.20.28" },
    });
    fireEvent.change(screen.getByPlaceholderText("••••••••"), {
      target: { value: "secret" },
    });
    fireEvent.change(screen.getByLabelText("Vendor"), {
      target: { value: "hikvision" },
    });
    fireEvent.click(screen.getByRole("button", { name: /add/i }));

    expect(state.mutate).toHaveBeenCalledTimes(1);
    const body = state.mutate.mock.calls[0][0];
    expect(body).toMatchObject({
      label: "Front NVR",
      ip: "192.168.20.28",
      rtsp_password: "secret",
      vendor: "hikvision",
    });
  });
});
