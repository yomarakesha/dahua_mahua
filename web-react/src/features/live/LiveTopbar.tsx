import {
  GridIcon,
  SearchIcon,
  PlayIcon,
  PauseIcon,
} from "@/components/icons";
import { useTranslation } from "react-i18next";

interface Props {
  /** Grid columns × rows (independent — build any N×M layout). */
  cols: number;
  rows: number;
  onCols: (n: number) => void;
  onRows: (n: number) => void;
  patrol: boolean;
  onTogglePatrol: () => void;
  patrolInterval: number;
  onCyclePatrolInterval: () => void;
  search: string;
  onSearch: (v: string) => void;
  /** How many cameras the current filter matches (a view count, NOT health). */
  showing: number;
  total: number;
}

function Stepper({
  label,
  value,
  onChange,
  fewerTitle,
  moreTitle,
}: {
  label: string;
  value: number;
  onChange: (n: number) => void;
  fewerTitle: string;
  moreTitle: string;
}) {
  return (
    <div className="flex h-[34px] items-center gap-1 rounded-lg border border-white/[.07] bg-panel pl-2 pr-1">
      <span className="text-2xs font-semibold uppercase tracking-wide text-ink-faint">{label}</span>
      <button
        type="button"
        onClick={() => onChange(value - 1)}
        title={fewerTitle}
        className="flex h-6 w-6 items-center justify-center rounded text-ink-mute transition hover:bg-white/[.06] hover:text-ink-soft"
      >
        −
      </button>
      <span className="w-4 text-center font-mono text-base font-semibold text-ink-soft">{value}</span>
      <button
        type="button"
        onClick={() => onChange(value + 1)}
        title={moreTitle}
        className="flex h-6 w-6 items-center justify-center rounded text-ink-mute transition hover:bg-white/[.06] hover:text-ink-soft"
      >
        +
      </button>
    </div>
  );
}

export function LiveTopbar({
  cols,
  rows,
  onCols,
  onRows,
  patrol,
  onTogglePatrol,
  patrolInterval,
  onCyclePatrolInterval,
  search,
  onSearch,
  showing,
  total,
}: Props) {
  const { t } = useTranslation();
  return (
    <div className="flex h-[54px] flex-none items-center gap-3.5 border-b border-white/[.06] bg-gradient-to-b from-[#0e1216] to-[#0b0e12] px-4">
      {/* layout controls: independent columns × rows */}
      <div className="flex items-center gap-1.5">
        <div className="flex h-[34px] items-center gap-1.5 rounded-lg border border-accent/30 bg-accent/[.12] px-3 text-base font-bold text-accent-light">
          <GridIcon size={14} />
          {t("live.liveGrid")}
        </div>
        <Stepper
          label={t("live.cols")}
          value={cols}
          onChange={onCols}
          fewerTitle={t("live.fewerCols")}
          moreTitle={t("live.moreCols")}
        />
        <span className="text-sm font-semibold text-ink-faint">×</span>
        <Stepper
          label={t("live.rows")}
          value={rows}
          onChange={onRows}
          fewerTitle={t("live.fewerRows")}
          moreTitle={t("live.moreRows")}
        />
      </div>

      <div className="h-6 w-px bg-white/[.08]" />

      {/* patrol */}
      <button
        type="button"
        onClick={onTogglePatrol}
        className={[
          "flex h-[34px] items-center gap-1.5 rounded-lg border px-3.5 text-base font-bold transition",
          patrol
            ? "border-accent/25 bg-accent/[.10] text-accent-light"
            : "border-white/[.07] bg-panel text-ink-mute hover:text-ink-soft",
        ].join(" ")}
      >
        {patrol ? <PauseIcon size={12} /> : <PlayIcon size={12} />}
        {t("live.patrol")}
      </button>
      <button
        type="button"
        onClick={onCyclePatrolInterval}
        title={t("live.patrolInterval")}
        className="flex h-[34px] items-center rounded-lg border border-white/[.07] bg-panel px-3 font-mono text-base font-semibold text-ink-mute transition hover:text-ink-soft"
      >
        {t("live.secondsShort", { count: patrolInterval })}
      </button>

      {/* search */}
      <label className="ml-auto flex h-[34px] w-[240px] items-center gap-2.5 rounded-lg border border-white/[.07] bg-bg px-3.5 focus-within:border-accent/30">
        <SearchIcon size={14} className="flex-none text-ink-faint" />
        <input
          type="search"
          aria-label={t("live.searchCameras")}
          value={search}
          onChange={(e) => onSearch(e.target.value)}
          placeholder={t("live.searchPlaceholder")}
          className="w-full bg-transparent text-base text-ink-soft placeholder:text-ink-faint focus:outline-none"
        />
      </label>

      {/* Filter/view count — how many cameras match the current NVR + search.
          Deliberately neutral (no green health dot): this is NOT a health metric,
          the app doesn't track per-stream connection state. */}
      <div
        className="flex h-[34px] items-center gap-2 rounded-lg border border-white/[.07] bg-panel px-3"
        title={t("live.filterMatchTooltip")}
      >
        <span className="text-2xs font-semibold uppercase tracking-wide text-ink-mute">
          {t("live.showing")}
        </span>
        <span className="font-mono text-base font-bold text-ink-soft">
          {showing}
          <span className="text-ink-faint">/{total}</span>
        </span>
      </div>
    </div>
  );
}
