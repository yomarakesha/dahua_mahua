/**
 * White-label / rebranding — runtime-configurable brand, no rebuild.
 *
 * The backend exposes GET /api/v1/branding (unauthenticated) which echoes the
 * deployment's BRAND_* settings. On boot we fetch it once and:
 *   - set document.title (+ favicon from logo_url if given),
 *   - drive the accent color via CSS variables the Tailwind `accent` token reads
 *     (so every green in the UI follows the brand),
 *   - expose {name, short, logo_url, …} to components via <BrandingProvider>.
 *
 * DEFAULTS reproduce today's look EXACTLY ("Kanagatly VMS" / "KM" / the green
 * accent). If the fetch fails or times out we keep those defaults, so the app
 * never renders unbranded and never blocks first paint on a broken request.
 */
import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { CONFIG } from "./config";

export interface Branding {
  name: string;
  short: string;
  primary: string; // #rrggbb
  accent: string; // #rrggbb (secondary / lighter accent)
  logo_url: string;
}

/** Baked-in fallback — MUST match the current design so an absent/failed fetch
 *  is pixel-identical to today. Keep in sync with backend Settings.brand_*. */
export const DEFAULT_BRANDING: Branding = {
  name: "Kanagatly VMS",
  short: "KM",
  primary: "#2ecc71",
  accent: "#43e088",
  logo_url: "",
};

let current: Branding = DEFAULT_BRANDING;

/** Current brand outside React (e.g. non-component modules). */
export function getBranding(): Branding {
  return current;
}

/** "Kanagatly VMS" → { head: "Kanagatly", tail: "VMS" } so the wordmark can
 *  accent the last word as the design does. Single-word names → tail only. */
export function splitBrandName(name: string): { head: string; tail: string } {
  const trimmed = name.trim();
  const i = trimmed.lastIndexOf(" ");
  if (i < 0) return { head: "", tail: trimmed };
  return { head: trimmed.slice(0, i), tail: trimmed.slice(i + 1) };
}

function hexToChannels(hex: string): [number, number, number] | null {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return null;
  const n = parseInt(m[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

/** Mix `ch` toward `target` (0=black, 255=white) by `amt` (0..1). */
function mixToward(ch: [number, number, number], target: number, amt: number): string {
  return ch.map((c) => Math.round(c + (target - c) * amt)).join(" ");
}

function setFavicon(url: string): void {
  if (typeof document === "undefined") return;
  let link = document.querySelector<HTMLLinkElement>('link[rel="icon"]');
  if (!link) {
    link = document.createElement("link");
    link.rel = "icon";
    document.head.appendChild(link);
  }
  link.href = url;
}

/** Apply a brand: update the module singleton, the CSS variables the accent
 *  token reads, the document title, and the favicon. Malformed colors are
 *  ignored (the CSS-var default — today's hex — stays), so this is safe. */
export function applyBranding(b: Branding): void {
  current = b;
  if (typeof document !== "undefined") {
    const root = document.documentElement;
    const primaryCh = hexToChannels(b.primary);
    if (primaryCh) root.style.setProperty("--brand-primary", primaryCh.join(" "));
    const accentCh = hexToChannels(b.accent);
    if (accentCh) root.style.setProperty("--brand-primary-light", accentCh.join(" "));
    // bright/dark are hand-picked shades of the default green; for a CUSTOM
    // primary we derive them so buttons/gradients follow the brand. For the
    // default primary we leave the CSS vars untouched → the exact original
    // #34d97e / #22b864 from index.css (keeps today's look pixel-identical).
    if (
      primaryCh &&
      b.primary.toLowerCase() !== DEFAULT_BRANDING.primary.toLowerCase()
    ) {
      root.style.setProperty("--brand-primary-bright", mixToward(primaryCh, 255, 0.08));
      root.style.setProperty("--brand-primary-dark", mixToward(primaryCh, 0, 0.12));
    }

    if (b.name) document.title = b.name;
    if (b.logo_url) setFavicon(b.logo_url);
  }
}

/** Fetch the deployment's brand. Rejects on non-2xx / network error / abort. */
export async function fetchBranding(signal?: AbortSignal): Promise<Branding> {
  const res = await fetch(CONFIG.backendBase + "/branding", { signal });
  if (!res.ok) throw new Error("branding " + res.status);
  const j = (await res.json()) as Partial<Branding>;
  return { ...DEFAULT_BRANDING, ...j };
}

const Ctx = createContext<Branding>(DEFAULT_BRANDING);

export function BrandingProvider({ children }: { children: ReactNode }) {
  const [brand, setBrand] = useState<Branding>(getBranding());

  useEffect(() => {
    // Apply the current (default) brand immediately so title + CSS vars are set
    // even before the fetch resolves — first paint stays branded (as today).
    applyBranding(getBranding());

    const ctrl = new AbortController();
    // Never let a hung backend keep the app on stale/blank branding.
    const timer = window.setTimeout(() => ctrl.abort(), 5000);
    fetchBranding(ctrl.signal)
      .then((b) => {
        applyBranding(b);
        setBrand(b);
      })
      .catch(() => {
        /* keep defaults — app already renders correctly */
      })
      .finally(() => window.clearTimeout(timer));

    return () => {
      window.clearTimeout(timer);
      ctrl.abort();
    };
  }, []);

  return <Ctx.Provider value={brand}>{children}</Ctx.Provider>;
}

/** Current brand for components. Defaults to today's brand with no provider,
 *  so isolated component tests render exactly as production defaults. */
export function useBranding(): Branding {
  return useContext(Ctx);
}
