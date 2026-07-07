import { useBranding, splitBrandName } from "@/lib/branding";

/** Brand monogram — a brand-colored disc with the short mark (default "KM"),
 *  or the brand's logo image when BRAND_LOGO_URL is configured. */
export function LogoMark({ size = 30 }: { size?: number }) {
  const brand = useBranding();
  if (brand.logo_url) {
    return (
      <img
        src={brand.logo_url}
        width={size}
        height={size}
        alt={brand.name}
        style={{ borderRadius: "50%", objectFit: "cover" }}
      />
    );
  }
  return (
    <svg width={size} height={size} viewBox="0 0 100 100" aria-label={brand.name}>
      <circle cx="50" cy="50" r="50" fill="rgb(var(--brand-primary))" />
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
        {brand.short}
      </text>
    </svg>
  );
}

export function LogoWordmark({ size = 30 }: { size?: number }) {
  const brand = useBranding();
  const { head, tail } = splitBrandName(brand.name);
  return (
    <div className="flex items-center gap-2.5">
      <LogoMark size={size} />
      <div className="text-[15px] font-extrabold tracking-tight text-ink-bright">
        {head}
        <span className="text-accent">{head ? " " : ""}{tail}</span>
      </div>
    </div>
  );
}
