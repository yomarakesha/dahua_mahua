/**
 * RecordingsCalendar — a date-picker popover that replaces the plain date <input>
 * in PlaybackPage. Days that have recordings (from useRecordingDays) are
 * highlighted; days without are dimmed. Navigating months re-queries the hook.
 * Picking a day commits it through `onSelect` (PlaybackPage keeps the seek reset
 * + viewMonth sync it already did for the input's onChange).
 *
 * The 1-based day numbers returned by the backend are NVR-local; the grid is laid
 * out in the same civil-calendar terms (UTC date math on the YYYY-MM-DD string),
 * so a highlighted "day 12" maps to the NVR-local 12th regardless of viewer tz.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useRecordingDays } from "@/api/hooks";
import { FilmIcon, ChevronRight } from "@/components/icons";

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

/** Shift a "YYYY-MM" string by `delta` months. */
function shiftMonth(ym: string, delta: number): string {
  const [y, m] = ym.split("-").map(Number);
  const idx = y * 12 + (m - 1) + delta;
  const ny = Math.floor(idx / 12);
  const nm = (idx % 12) + 1;
  return `${ny}-${pad2(nm)}`;
}

export interface RecordingsCalendarProps {
  nvrId: string | null;
  /** 1-based NVR channel (0 when no camera picked). */
  channel: number;
  /** Currently selected date "YYYY-MM-DD". */
  selectedDate: string;
  onSelect: (date: string) => void;
  /** Oldest selectable date "YYYY-MM-DD" (from retention), or null. */
  minDate?: string | null;
  /** Newest selectable date "YYYY-MM-DD" (today). */
  maxDate: string;
  /** Disabled until a camera is chosen. */
  disabled?: boolean;
}

export default function RecordingsCalendar({
  nvrId,
  channel,
  selectedDate,
  onSelect,
  minDate,
  maxDate,
  disabled = false,
}: RecordingsCalendarProps) {
  const { t, i18n } = useTranslation();
  const [open, setOpen] = useState(false);
  /** Month currently shown in the grid ("YYYY-MM"). Follows selectedDate on open. */
  const [displayMonth, setDisplayMonth] = useState(() => selectedDate.slice(0, 7));
  const rootRef = useRef<HTMLDivElement>(null);

  const hasSelection = !!nvrId && channel > 0;

  // Re-queries automatically when displayMonth changes (key in the queryKey).
  const { data: daysData } = useRecordingDays(
    nvrId ?? "",
    channel,
    displayMonth,
    hasSelection && open,
  );
  const recordingDays = useMemo(
    () => new Set(daysData?.days ?? []),
    [daysData],
  );
  const monthLoaded = !!daysData;
  const noRecordings = monthLoaded && (daysData?.days.length ?? 0) === 0;

  // Snap the grid back to the selected month whenever the popover (re)opens.
  useEffect(() => {
    if (open) setDisplayMonth(selectedDate.slice(0, 7));
  }, [open, selectedDate]);

  // Close on outside click / Escape.
  useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  // ── Grid geometry (UTC civil-calendar math on the YYYY-MM string) ────────────
  const [gy, gm] = displayMonth.split("-").map(Number);
  const daysInMonth = new Date(Date.UTC(gy, gm, 0)).getUTCDate();
  const firstWeekday = new Date(Date.UTC(gy, gm - 1, 1)).getUTCDay(); // 0=Sun

  const weekdayLabels = t("playback.weekdaysShort").split(/\s+/);

  const monthTitle = useMemo(() => {
    try {
      return new Intl.DateTimeFormat(i18n.language, {
        year: "numeric",
        month: "long",
        timeZone: "UTC",
      }).format(new Date(Date.UTC(gy, gm - 1, 1)));
    } catch {
      return displayMonth;
    }
  }, [i18n.language, gy, gm, displayMonth]);

  const minMonth = minDate ? minDate.slice(0, 7) : null;
  const maxMonth = maxDate.slice(0, 7);
  const prevDisabled = !!minMonth && displayMonth <= minMonth;
  const nextDisabled = displayMonth >= maxMonth;

  const cells: (number | null)[] = [];
  for (let i = 0; i < firstWeekday; i++) cells.push(null);
  for (let d = 1; d <= daysInMonth; d++) cells.push(d);

  return (
    <div className="relative flex flex-col gap-0.5" ref={rootRef}>
      <span className="text-[10px] font-semibold uppercase tracking-wider text-ink-dim">
        {t("playback.date")}
      </span>
      <button
        type="button"
        aria-label={t("playback.date")}
        aria-haspopup="dialog"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        className="flex h-8 items-center gap-2 rounded-md border border-white/[.08] bg-[#161b22] px-2 text-sm text-ink-soft transition focus:outline-none focus:ring-1 focus:ring-accent/50 hover:border-white/[.16] disabled:cursor-not-allowed disabled:opacity-40"
      >
        <FilmIcon size={14} className="text-ink-dim" />
        <span className="tabular-nums">{selectedDate}</span>
      </button>

      {open && (
        <div
          role="dialog"
          aria-label={t("playback.openCalendar")}
          className="absolute left-0 top-full z-30 mt-1 w-64 rounded-lg border border-white/[.1] bg-[#12171e] p-3 shadow-2xl shadow-black/50"
        >
          {/* Month nav */}
          <div className="mb-2 flex items-center justify-between">
            <button
              type="button"
              aria-label={t("playback.previousMonth")}
              title={t("playback.previousMonth")}
              disabled={prevDisabled}
              onClick={() => setDisplayMonth((m) => shiftMonth(m, -1))}
              className="flex h-6 w-6 items-center justify-center rounded text-ink-dim transition hover:bg-white/[.06] hover:text-ink-soft disabled:cursor-not-allowed disabled:opacity-30"
            >
              <ChevronRight size={16} className="rotate-180" />
            </button>
            <span className="text-sm font-semibold capitalize text-ink-soft">{monthTitle}</span>
            <button
              type="button"
              aria-label={t("playback.nextMonth")}
              title={t("playback.nextMonth")}
              disabled={nextDisabled}
              onClick={() => setDisplayMonth((m) => shiftMonth(m, 1))}
              className="flex h-6 w-6 items-center justify-center rounded text-ink-dim transition hover:bg-white/[.06] hover:text-ink-soft disabled:cursor-not-allowed disabled:opacity-30"
            >
              <ChevronRight size={16} />
            </button>
          </div>

          {/* Weekday header */}
          <div className="mb-1 grid grid-cols-7 gap-0.5">
            {weekdayLabels.map((w, i) => (
              <div
                key={i}
                className="text-center text-[10px] font-medium uppercase tracking-wide text-ink-faint"
              >
                {w}
              </div>
            ))}
          </div>

          {/* Day grid */}
          <div className="grid grid-cols-7 gap-0.5">
            {cells.map((day, i) => {
              if (day === null) return <div key={`e-${i}`} />;
              const dateStr = `${displayMonth}-${pad2(day)}`;
              const hasRecording = recordingDays.has(day);
              const inRange = (!minDate || dateStr >= minDate) && dateStr <= maxDate;
              const isSelected = dateStr === selectedDate;
              return (
                <button
                  key={dateStr}
                  type="button"
                  data-has-recording={hasRecording}
                  aria-current={isSelected ? "date" : undefined}
                  aria-label={dateStr}
                  disabled={!inRange}
                  onClick={() => {
                    onSelect(dateStr);
                    setOpen(false);
                  }}
                  className={[
                    "flex h-8 items-center justify-center rounded text-sm tabular-nums transition disabled:cursor-not-allowed disabled:opacity-20",
                    isSelected
                      ? "bg-accent text-white ring-1 ring-accent"
                      : hasRecording
                      ? "bg-accent/[.16] font-semibold text-accent-light ring-1 ring-accent/30 hover:bg-accent/[.26]"
                      : "text-ink-dim hover:bg-white/[.05] hover:text-ink-soft",
                  ].join(" ")}
                >
                  {day}
                </button>
              );
            })}
          </div>

          {noRecordings && (
            <div className="mt-2 text-center text-[11px] text-ink-faint">
              {t("playback.noRecordingsThisMonth")}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
