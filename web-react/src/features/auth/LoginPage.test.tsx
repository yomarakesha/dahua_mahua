import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "@/lib/auth";
import LoginPage from "./LoginPage";

describe("LoginPage", () => {
  it("renders the sign-in form without crashing", () => {
    render(
      <MemoryRouter initialEntries={["/login"]}>
        <AuthProvider>
          <LoginPage />
        </AuthProvider>
      </MemoryRouter>,
    );
    // Branding + a submit affordance are present.
    expect(screen.getAllByText(/sign in/i).length).toBeGreaterThan(0);
    expect(screen.getByLabelText(/username/i) || screen.getByPlaceholderText(/admin/i)).toBeTruthy();
  });
});
