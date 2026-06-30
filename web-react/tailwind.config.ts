import type { Config } from "tailwindcss";

/**
 * Design tokens extracted from the Kanagatly VMS designer export.
 * Dark "security console" theme, green accent, compact data-dense type.
 */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // surfaces
        bg: "#0a0d10",
        panel: "#11161a",
        deep: "#060708",
        "green-tint": "#04130a",
        // accent (KANAGATLY green)
        accent: {
          DEFAULT: "#2ecc71",
          light: "#43e088",
          bright: "#34d97e",
          dark: "#22b864",
        },
        // text ramp
        ink: "#e7edf1",
        "ink-bright": "#f1f5f7",
        "ink-soft": "#cdd6db",
        "ink-mute": "#9aa6ad",
        "ink-dim": "#7c878e",
        "ink-faint": "#5e6a71",
        // status
        warn: "#e0a030",
        danger: "#e76b5e",
      },
      fontFamily: {
        sans: ['Manrope', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'monospace'],
      },
      fontSize: {
        "2xs": ["10px", "14px"],
        "3xs": ["8px", "11px"],
        xs: ["11px", "15px"],
        sm: ["12px", "16px"],
        base: ["13px", "18px"],
      },
      borderRadius: {
        sm: "4px",
        DEFAULT: "7px",
        md: "8px",
        lg: "9px",
        xl: "11px",
        "2xl": "14px",
        "3xl": "18px",
      },
      boxShadow: {
        panel: "0 1px 0 rgba(255,255,255,.03), 0 8px 24px rgba(0,0,0,.4)",
        glow: "0 0 0 1px rgba(46,204,113,.25), 0 6px 20px rgba(46,204,113,.12)",
      },
    },
  },
  plugins: [],
} satisfies Config;
