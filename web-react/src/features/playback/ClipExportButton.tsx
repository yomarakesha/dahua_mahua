/**
 * ClipExportButton — downloads the currently selected timeline range as a clip.
 *
 * The selection comes from Timeline's shift-drag (already clamped to ≤10 min and
 * ≤now via clampClipRange). Export is a REAL-TIME server-side pull: the recorder
 * streams the footage back at 1× so a 10-minute clip takes ~10 minutes — hence
 * the prominent slow warning and the indeterminate "preparing…" state while the
 * request is in flight.
 *
 * We fetch() the clip URL (with the JWT as ?token=, the only auth channel a
 * download link has) rather than navigating directly, so we can surface a 429
 * ("recorder busy") distinctly and show progress. On success the blob is saved
 * via a synthetic <a download>.
 */
import { useCallback, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { CONFIG } from "@/lib/config";
import { getToken } from "@/api/client";
import { buildClipUrl } from "./playback-utils";
import { FilmIcon, XIcon } from "@/components/icons";

type ExportState = "idle" | "preparing" | "busy" | "failed";

export interface ClipExportButtonProps {
  nvrId: string;
  channel: number;
  selection: { start: number; end: number };
  /** Clears the selection (hides this control). */
  onClear: () => void;
}

export default function ClipExportButton({
  nvrId,
  channel,
  selection,
  onClear,
}: ClipExportButtonProps) {
  const { t } = useTranslation();
  const [state, setState] = useState<ExportState>("idle");
  // Guards against overlapping exports (the request can run for minutes).
  const inFlightRef = useRef(false);

  const handleExport = useCallback(async () => {
    if (inFlightRef.current) return;
    const token = getToken();
    if (!token) {
      setState("failed");
      return;
    }
    inFlightRef.current = true;
    setState("preparing");
    const url = buildClipUrl(
      CONFIG.backendBase,
      nvrId,
      channel,
      selection.start,
      selection.end,
      token,
    );
    try {
      const res = await fetch(url);
      if (res.status === 429) {
        setState("busy");
        return;
      }
      if (!res.ok) {
        setState("failed");
        return;
      }
      const blob = await res.blob();
      // Trigger the browser "Save as" from the in-memory blob.
      if (typeof URL !== "undefined" && URL.createObjectURL) {
        const objUrl = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = objUrl;
        a.download = `clip_${nvrId}_ch${channel}_${selection.start}-${selection.end}.mp4`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(objUrl), 10_000);
      }
      setState("idle");
    } catch {
      setState("failed");
    } finally {
      inFlightRef.current = false;
    }
  }, [nvrId, channel, selection.start, selection.end]);

  const preparing = state === "preparing";

  return (
    <div className="flex items-center gap-2">
      <button
        type="button"
        aria-label={t("playback.exportClip")}
        title={t("playback.exportClipHint")}
        disabled={preparing}
        onClick={() => void handleExport()}
        className="flex h-8 items-center gap-1.5 rounded-md bg-accent/[.16] px-3 text-sm font-semibold text-accent-light ring-1 ring-accent/30 transition hover:bg-accent/[.24] disabled:cursor-not-allowed disabled:opacity-60"
      >
        {preparing ? (
          <span className="h-3 w-3 animate-spin rounded-full border border-accent-light/60 border-t-transparent" />
        ) : (
          <FilmIcon size={14} />
        )}
        {preparing ? t("playback.exportPreparing") : t("playback.exportClip")}
      </button>

      {/* Slow-pull warning — always visible so the operator knows before clicking. */}
      <span className="max-w-[22rem] text-[11px] leading-tight text-ink-dim">
        {state === "busy" ? (
          <span className="font-semibold text-warn">{t("playback.exportBusy")}</span>
        ) : state === "failed" ? (
          <span className="font-semibold text-danger">{t("playback.exportFailed")}</span>
        ) : (
          t("playback.exportClipHint")
        )}
      </span>

      <button
        type="button"
        aria-label={t("playback.clearSelection")}
        title={t("playback.clearSelection")}
        onClick={onClear}
        className="flex h-6 w-6 items-center justify-center rounded text-ink-dim transition hover:bg-white/[.06] hover:text-ink-soft"
      >
        <XIcon size={13} />
      </button>
    </div>
  );
}
