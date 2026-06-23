import {
  GridIcon,
  SearchIcon,
  ChevronDown,
  PlayIcon,
  PauseIcon,
} from "@/components/icons";

interface Props {
  /** sqrt of current preset (N for an N×N grid). */
  gridN: number;
  onCycleGrid: () => void;
  patrol: boolean;
  onTogglePatrol: () => void;
  patrolInterval: number;
  onCyclePatrolInterval: () => void;
  search: string;
  onSearch: (v: string) => void;
  online: number;
  total: number;
}

export function LiveTopbar({
  gridN,
  onCycleGrid,
  patrol,
  onTogglePatrol,
  patrolInterval,
  onCyclePatrolInterval,
  search,
  onSearch,
  online,
  total,
}: Props) {
  return (
    <div className="flex h-[54px] flex-none items-center gap-3.5 border-b border-white/[.06] bg-gradient-to-b from-[#0e1216] to-[#0b0e12] px-4">
      {/* layout controls */}
      <div className="flex items-center gap-1.5">
        <div className="flex h-[34px] items-center gap-1.5 rounded-lg border border-accent/30 bg-accent/[.12] px-3 text-base font-bold text-accent-light">
          <GridIcon size={14} />
          Live Grid
        </div>
        <button
          type="button"
          onClick={onCycleGrid}
          title="Cycle layout"
          className="flex h-[34px] items-center gap-1.5 rounded-lg border border-white/[.07] bg-panel px-3 font-mono text-base font-semibold text-ink-mute transition hover:border-white/[.14] hover:text-ink-soft"
        >
          {gridN}×{gridN}
          <ChevronDown size={13} className="text-ink-dim" />
        </button>
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
        Patrol
      </button>
      <button
        type="button"
        onClick={onCyclePatrolInterval}
        title="Patrol interval"
        className="flex h-[34px] items-center rounded-lg border border-white/[.07] bg-panel px-3 font-mono text-base font-semibold text-ink-mute transition hover:text-ink-soft"
      >
        {patrolInterval}s
      </button>

      {/* search */}
      <label className="ml-auto flex h-[34px] w-[240px] items-center gap-2.5 rounded-lg border border-white/[.07] bg-bg px-3.5 focus-within:border-accent/30">
        <SearchIcon size={14} className="flex-none text-ink-faint" />
        <input
          value={search}
          onChange={(e) => onSearch(e.target.value)}
          placeholder="Search cameras…"
          className="w-full bg-transparent text-base text-ink-soft placeholder:text-ink-faint focus:outline-none"
        />
      </label>

      {/* online count */}
      <div className="flex h-[34px] items-center gap-2 rounded-lg border border-accent/25 bg-accent/[.10] px-3">
        <span className="h-[7px] w-[7px] rounded-full bg-accent shadow-[0_0_8px_#2ecc71]" />
        <span className="font-mono text-base font-bold text-[#cfe9da]">
          {online}
          <span className="text-ink-faint">/{total}</span>
        </span>
      </div>
    </div>
  );
}
