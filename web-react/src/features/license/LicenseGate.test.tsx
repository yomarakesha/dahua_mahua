/**
 * LicenseGate state tests — mocks GET /license and asserts the gate renders the
 * app, the full-screen block, or the grace banner for each state, and that it
 * fails open (renders the app) while loading or on a fetch error.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "@/lib/auth";
import type { LicenseState, LicenseStatus } from "./api";

const fetchLicense = vi.fn();
vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return { ...actual, fetchLicense: () => fetchLicense() };
});

// Import AFTER the mock is registered.
import { LicenseGate } from "./LicenseGate";

function makeStatus(state: LicenseState, over: Partial<LicenseStatus> = {}): LicenseStatus {
  return {
    valid: state === "valid",
    reason: state,
    customer: null,
    site_id: null,
    issued: null,
    expires: null,
    features: [],
    limits: { max_cameras: null, max_nvrs: null },
    days_left: null,
    fingerprint: "abc123fingerprint",
    state,
    enforced: true,
    grace_days_left: null,
    ...over,
  };
}

function renderGate() {
  return render(
    <MemoryRouter>
      <AuthProvider>
        <LicenseGate>
          <div data-testid="app">APP CONTENT</div>
        </LicenseGate>
      </AuthProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => fetchLicense.mockReset());
afterEach(() => vi.restoreAllMocks());

describe("LicenseGate", () => {
  it("renders the app when enforcement is OFF regardless of state", async () => {
    fetchLicense.mockResolvedValue(makeStatus("missing", { enforced: false }));
    renderGate();
    await waitFor(() => expect(screen.getByTestId("app")).toBeInTheDocument());
    expect(screen.queryByText("License required")).not.toBeInTheDocument();
  });

  it("renders the app when the license is valid", async () => {
    fetchLicense.mockResolvedValue(makeStatus("valid"));
    renderGate();
    await waitFor(() => expect(screen.getByTestId("app")).toBeInTheDocument());
  });

  it.each(["missing", "expired", "invalid", "mismatch"] as LicenseState[])(
    "blocks the app with a full-screen notice when enforced + %s",
    async (state) => {
      fetchLicense.mockResolvedValue(makeStatus(state));
      renderGate();
      await waitFor(() => expect(screen.getByText("License required")).toBeInTheDocument());
      expect(screen.queryByTestId("app")).not.toBeInTheDocument();
      // The machine fingerprint must be visible so an operator can send it out.
      expect(screen.getByText("abc123fingerprint")).toBeInTheDocument();
    },
  );

  it("renders the app plus a grace banner during the grace period", async () => {
    fetchLicense.mockResolvedValue(makeStatus("grace", { grace_days_left: 5 }));
    renderGate();
    await waitFor(() => expect(screen.getByTestId("app")).toBeInTheDocument());
    expect(screen.getByText(/5 day/i)).toBeInTheDocument();
  });

  it("fails open (renders the app) when the status fetch errors", async () => {
    fetchLicense.mockRejectedValue(new Error("network"));
    renderGate();
    await waitFor(() => expect(screen.getByTestId("app")).toBeInTheDocument());
    expect(screen.queryByText("License required")).not.toBeInTheDocument();
  });

  it("swaps to the block screen when a 402 window event fires", async () => {
    fetchLicense.mockResolvedValue(makeStatus("valid"));
    renderGate();
    await waitFor(() => expect(screen.getByTestId("app")).toBeInTheDocument());
    act(() => {
      window.dispatchEvent(new CustomEvent("license-blocked", { detail: { state: "expired" } }));
    });
    await waitFor(() => expect(screen.getByText("License required")).toBeInTheDocument());
  });
});
