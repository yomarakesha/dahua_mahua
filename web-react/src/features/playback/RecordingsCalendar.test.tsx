import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import RecordingsCalendar from "./RecordingsCalendar";

// Mock the recordings-days hook — the calendar highlights the returned day numbers.
const mockDays = vi.hoisted(() => ({ days: [5, 12, 20] as number[] }));
vi.mock("@/api/hooks", () => ({
  useRecordingDays: () => ({ data: { month: "2026-07", days: mockDays.days }, isLoading: false }),
}));

function renderCal(props: Partial<React.ComponentProps<typeof RecordingsCalendar>> = {}) {
  const onSelect = vi.fn();
  render(
    <RecordingsCalendar
      nvrId="nvr1"
      channel={1}
      selectedDate="2026-07-10"
      onSelect={onSelect}
      minDate="2026-01-01"
      maxDate="2026-07-31"
      {...props}
    />,
  );
  return { onSelect };
}

describe("RecordingsCalendar", () => {
  it("highlights days returned by the hook (data-has-recording) and dims others", () => {
    renderCal();
    fireEvent.click(screen.getByRole("button", { name: /date/i })); // open popover
    // Days 5/12/20 have recordings; day 6 does not.
    expect(screen.getByRole("button", { name: "2026-07-05" }).getAttribute("data-has-recording")).toBe("true");
    expect(screen.getByRole("button", { name: "2026-07-12" }).getAttribute("data-has-recording")).toBe("true");
    expect(screen.getByRole("button", { name: "2026-07-06" }).getAttribute("data-has-recording")).toBe("false");
  });

  it("clicking a day commits the date and closes the popover", () => {
    const { onSelect } = renderCal();
    fireEvent.click(screen.getByRole("button", { name: /date/i }));
    fireEvent.click(screen.getByRole("button", { name: "2026-07-20" }));
    expect(onSelect).toHaveBeenCalledWith("2026-07-20");
    // Popover closed → day buttons gone.
    expect(screen.queryByRole("button", { name: "2026-07-20" })).toBeNull();
  });

  it("shows 'no recordings this month' when the hook returns an empty month", () => {
    mockDays.days = [];
    renderCal();
    fireEvent.click(screen.getByRole("button", { name: /date/i }));
    expect(screen.getByText(/no recordings this month/i)).toBeTruthy();
    mockDays.days = [5, 12, 20]; // restore for any later test ordering
  });
});
