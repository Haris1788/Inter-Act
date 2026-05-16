import type { Config } from "tailwindcss";

// Ported verbatim from `Inter Act Node-red/styles-v2.css` :root.
// This is the single source of truth for the design system. lib/tokens.ts
// re-exports the same hex values for Konva/SVG so canvas colors == CSS colors.
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: {
          app: "#F1EFE8",
          canvas: "#FAF8F2",
          panel: "#FFFFFF",
          surface: "#FFFFFF",
          elevated: "#FFFFFF",
          hover: "#F2F1EC",
          sunken: "#EEEBE3",
          recessed: "#E7E4DA",
        },
        line: {
          DEFAULT: "#E5E3DD",
          soft: "#EFEDE7",
          strong: "#C9C6BD",
          emboss: "#D6D3CA",
        },
        ink: {
          DEFAULT: "#18181B",
          2: "#3F3F46",
          3: "#6B6B72",
          4: "#9A9A9F",
          5: "#BFBFC2",
        },
        ok: "#5F8F58",
        "ok-bg": "#E6EBE0",
        warn: "#B08742",
        "warn-bg": "#F0E6D0",
        err: "#A4503B",
        "err-bg": "#EEDAD1",
        idle: "#8A8A90",
        "idle-bg": "#E7E6E2",
        info: "#5C6B7A",
        cta: "#18181B",
        "cta-2": "#27272A",
      },
      borderRadius: {
        sm: "6px",
        md: "8px",
        lg: "12px",
        xl: "16px",
      },
      boxShadow: {
        e1: "0 1px 0 rgba(24,24,27,0.03), 0 1px 2px rgba(24,24,27,0.04)",
        e2: "0 1px 0 rgba(24,24,27,0.03), 0 3px 10px rgba(24,24,27,0.05)",
        e3: "0 6px 22px rgba(24,24,27,0.08), 0 2px 4px rgba(24,24,27,0.04)",
      },
      fontFamily: {
        sans: [
          '"IBM Plex Sans"',
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          '"Segoe UI"',
          "sans-serif",
        ],
        mono: [
          '"IBM Plex Mono"',
          "ui-monospace",
          '"SF Mono"',
          "Menlo",
          "monospace",
        ],
      },
      fontSize: {
        "10": "10px",
        "10.5": "10.5px",
        "11": "11px",
        "11.5": "11.5px",
        "12": "12px",
        "12.5": "12.5px",
        "13": "13px",
        "14": "14px",
      },
    },
  },
  plugins: [],
} satisfies Config;
