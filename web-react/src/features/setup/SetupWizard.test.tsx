import { afterEach, describe, expect, it } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "@/lib/auth";
import type { Me } from "@/api/types";
import SetupWizard from "./SetupWizard";

const ADMIN_MUST_CHANGE: Me = {
  id: "u1",
  username: "admin",
  role: "admin",
  is_active: true,
  must_change_password: true,
  created_at: "2026-01-01T00:00:00Z",
  last_login_at: null,
  region_ids: [],
  camera_ids: [],
};

function renderWizard() {
  localStorage.setItem("dss_me", JSON.stringify(ADMIN_MUST_CHANGE));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/setup"]}>
        <AuthProvider>
          <SetupWizard />
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("SetupWizard", () => {
  afterEach(() => {
    cleanup();
    localStorage.clear();
  });

  it("opens on the change-password step for an admin on the bootstrap password", () => {
    renderWizard();
    expect(screen.getByText(/change the default password/i)).toBeInTheDocument();
    // 3-step flow: secure account → add NVR → done.
    expect(screen.getByText(/step 1 of 3/i)).toBeInTheDocument();
  });

  it("validates the new password length before submitting", () => {
    renderWizard();
    const inputs = screen.getAllByDisplayValue("");
    // current, new, confirm password fields.
    fireEvent.change(inputs[0], { target: { value: "admin" } });
    fireEvent.change(inputs[1], { target: { value: "short" } });
    fireEvent.change(inputs[2], { target: { value: "short" } });
    fireEvent.click(screen.getByRole("button", { name: /save & continue/i }));
    expect(screen.getByText(/at least 8 characters/i)).toBeInTheDocument();
  });
});
