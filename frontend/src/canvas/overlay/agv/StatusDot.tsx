// StatusDot — shared pulsing status indicator for all AGV silhouette components.
// Replaces the inline static <Circle> in ShuttleAgv, ForkliftAgv, TowAgv.

import { useEffect, useRef, useState } from "react";
import { Circle } from "react-konva";

// ── Color map (single canonical copy) ───────────────────────────────────────

export const STATUS_DOT_COLORS: Record<string, string> = {
  idle:      "#16a085",
  moving:    "#2563eb",
  loading:   "#eab308",
  unloading: "#eab308",
  returning: "#3b82f6",
  faulted:   "#dc2626",
  stopped:   "#6b7280",
  ok:        "#16a085",
  warn:      "#eab308",
  busy:      "#2563eb",
  down:      "#dc2626",
};
const DEFAULT_DOT_COLOR = "#9ca3af";

export function dotColor(status: string): string {
  return STATUS_DOT_COLORS[status] ?? DEFAULT_DOT_COLOR;
}

// ── Pulse profile per status group ──────────────────────────────────────────

// "stopped" → no pulse (static).
// "idle" / "ok" → slow, subtle (2 s period, radius 4→5, alpha 0.7→1.0).
// "moving" / "returning" / "loading" / "unloading" / "busy" → fast (0.8 s, radius 4→6, alpha 0.7→1.0).
// "faulted" / "down" → urgent (0.5 s, radius 4→7, alpha 0.7→1.0).

type PulseGroup = "none" | "slow" | "fast" | "urgent";

function pulseGroup(status: string): PulseGroup {
  switch (status) {
    case "stopped":
      return "none";
    case "idle":
    case "ok":
      return "slow";
    case "moving":
    case "returning":
    case "loading":
    case "unloading":
    case "busy":
      return "fast";
    case "faulted":
    case "down":
      return "urgent";
    default:
      return "none";
  }
}

interface PulseParams {
  periodMs: number;
  rMin: number;
  rMax: number;
  alphaMin: number;
  alphaMax: number;
}

const PULSE_PARAMS: Record<PulseGroup, PulseParams> = {
  none:   { periodMs: 2000, rMin: 4, rMax: 4,   alphaMin: 1.0, alphaMax: 1.0 },
  slow:   { periodMs: 2000, rMin: 4, rMax: 5,   alphaMin: 0.7, alphaMax: 1.0 },
  fast:   { periodMs:  800, rMin: 4, rMax: 6,   alphaMin: 0.7, alphaMax: 1.0 },
  urgent: { periodMs:  500, rMin: 4, rMax: 7,   alphaMin: 0.7, alphaMax: 1.0 },
};

// ── Component ────────────────────────────────────────────────────────────────

interface StatusDotProps {
  x: number;
  y: number;
  status: string;
}

export function StatusDot({ x, y, status }: StatusDotProps) {
  const group = pulseGroup(status);
  const params = PULSE_PARAMS[group];
  const fill = dotColor(status);

  // Animated phase — only ticks when group !== "none"
  const phaseRef = useRef(0);
  const rafRef = useRef<number | null>(null);
  const lastTsRef = useRef<number | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (group === "none") {
      // Cancel any running RAF and stay static
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
      lastTsRef.current = null;
      return;
    }

    let cancelled = false;

    function loop(ts: number) {
      if (cancelled) return;
      if (lastTsRef.current !== null) {
        phaseRef.current += ts - lastTsRef.current;
      }
      lastTsRef.current = ts;
      setTick((n) => n + 1);
      rafRef.current = requestAnimationFrame(loop);
    }

    rafRef.current = requestAnimationFrame(loop);

    return () => {
      cancelled = true;
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
      lastTsRef.current = null;
    };
  // Re-run when the pulse group changes (status change)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [group]);

  // Suppress unused-variable lint for tick — it's only used to trigger re-render
  void tick;

  const t = group === "none"
    ? 0
    : (Math.sin((phaseRef.current / params.periodMs) * 2 * Math.PI) + 1) / 2;

  const radius = params.rMin + t * (params.rMax - params.rMin);
  const opacity = params.alphaMin + t * (params.alphaMax - params.alphaMin);

  return (
    <Circle
      x={x}
      y={y}
      radius={radius}
      fill={fill}
      stroke="rgba(0,0,0,0.35)"
      strokeWidth={0.8}
      opacity={opacity}
    />
  );
}
