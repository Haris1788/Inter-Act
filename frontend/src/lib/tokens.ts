// Hex palette mirror of tailwind.config.ts / styles-v2.css :root.
// Konva and inline SVG cannot read Tailwind classes - they import from here so
// canvas-rendered colors stay identical to DOM-rendered colors.
export const tokens = {
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
    base: "#E5E3DD",
    soft: "#EFEDE7",
    strong: "#C9C6BD",
    emboss: "#D6D3CA",
  },
  ink: {
    1: "#18181B",
    2: "#3F3F46",
    3: "#6B6B72",
    4: "#9A9A9F",
    5: "#BFBFC2",
  },
  ok: "#5F8F58",
  okBg: "#E6EBE0",
  warn: "#B08742",
  warnBg: "#F0E6D0",
  err: "#A4503B",
  errBg: "#EEDAD1",
  idle: "#8A8A90",
  idleBg: "#E7E6E2",
  info: "#5C6B7A",
  infoBg: "#E4E8EC",
  cta: "#18181B",
  cta2: "#27272A",
  // field-layer accents (from the SVG defs in INTER.act v2.html)
  haloArm: "#18181B",
  haloPool: "#5F8F58",
  poolStroke: "#A6BDA0",
  trace: "#9A9A9F",
} as const;
