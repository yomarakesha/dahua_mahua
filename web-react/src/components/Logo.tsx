/** KANAGATLY "KM" monogram — green disc, white mark. */
export function LogoMark({ size = 30 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 100 100" aria-label="Kanagatly">
      <circle cx="50" cy="50" r="50" fill="#2ecc71" />
      <text
        x="50"
        y="50"
        dominantBaseline="central"
        textAnchor="middle"
        fontFamily="Manrope, system-ui, sans-serif"
        fontSize="42"
        fontWeight={800}
        fill="#fff"
        letterSpacing="-2"
      >
        KM
      </text>
    </svg>
  );
}

export function LogoWordmark({ size = 30 }: { size?: number }) {
  return (
    <div className="flex items-center gap-2.5">
      <LogoMark size={size} />
      <div className="text-[15px] font-extrabold tracking-tight text-ink-bright">
        Kanagatly<span className="text-accent"> VMS</span>
      </div>
    </div>
  );
}
