import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "@/lib/auth";
import { BrandingProvider, DEFAULT_BRANDING } from "@/lib/branding";
import { AppShell } from "./AppShell";

describe("AppShell", () => {
  it("renders the brand name in the header (defaults to today's brand)", () => {
    render(
      <MemoryRouter>
        <BrandingProvider>
          <AuthProvider>
            <AppShell />
          </AuthProvider>
        </BrandingProvider>
      </MemoryRouter>,
    );
    // Logo mark labels itself with the full brand name; the wordmark splits it
    // (head + accented tail), so assert the head token is present too.
    expect(screen.getByLabelText(DEFAULT_BRANDING.name)).toBeInTheDocument();
    expect(screen.getByText("Kanagatly")).toBeInTheDocument();
  });
});
