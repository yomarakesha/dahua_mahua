/**
 * NvrTable tests — vendor UX fix:
 *   - Each row exposes an inline Vendor selector.
 *   - Changing it PATCHes the NVR with the new vendor and auto-runs Test so the
 *     operator can re-validate the RTSP path before re-enabling.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { Nvr } from "@/api/types";

const state = vi.hoisted(() => ({
  // update.mutate calls onSuccess synchronously so the auto-Test fires.
  updateMutate: vi.fn((_vars: unknown, opts?: { onSuccess?: () => void }) => opts?.onSuccess?.()),
  testMutate: vi.fn(),
  delMutate: vi.fn(),
}));

vi.mock("@/api/hooks", () => ({
  useUpdateNvr: () => ({ mutate: state.updateMutate, isPending: false, isError: false, error: null }),
  useTestNvr: () => ({ mutate: state.testMutate, isPending: false }),
  useDeleteNvr: () => ({ mutate: state.delMutate, isPending: false, isError: false, error: null }),
}));

import { NvrTable } from "./NvrTable";

const NVR: Nvr = {
  id: "nvr1",
  label: "Front NVR",
  ip: "192.168.20.28",
  port: 554,
  rtsp_username: "admin",
  vendor: "dahua",
  enabled: false,
  group: null,
  region_id: null,
  created_at: "",
  updated_at: "",
  camera_count: 4,
  create_notice: null,
};

function renderTable() {
  render(
    <MemoryRouter>
      <NvrTable nvrs={[NVR]} showHealth={false} health={{}} />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  state.updateMutate.mockClear();
  state.testMutate.mockClear();
});

describe("NvrTable — inline vendor change", () => {
  it("renders the vendor as an editable select seeded to the current vendor", () => {
    renderTable();
    const vendor = screen.getByLabelText("Vendor") as HTMLSelectElement;
    expect(vendor.value).toBe("dahua");
    expect(screen.getByRole("option", { name: "hikvision" })).toBeTruthy();
  });

  it("changing the vendor PATCHes with the new vendor and auto-triggers Test", () => {
    renderTable();
    fireEvent.change(screen.getByLabelText("Vendor"), { target: { value: "hikvision" } });

    expect(state.updateMutate).toHaveBeenCalledTimes(1);
    expect(state.updateMutate.mock.calls[0][0]).toEqual({
      id: "nvr1",
      body: { vendor: "hikvision" },
    });
    // onSuccess auto-runs Test to re-validate the new vendor's RTSP path.
    expect(state.testMutate).toHaveBeenCalledWith("nvr1", expect.anything());
    // Recovery hint guides the operator to enable after a passing test.
    expect(screen.getByText(/enable this NVR/i)).toBeTruthy();
  });

  it("selecting the same vendor is a no-op (no PATCH)", () => {
    renderTable();
    fireEvent.change(screen.getByLabelText("Vendor"), { target: { value: "dahua" } });
    expect(state.updateMutate).not.toHaveBeenCalled();
  });
});
