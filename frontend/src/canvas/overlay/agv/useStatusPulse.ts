// useStatusPulse — shared hook for the inline AGV status dot animation.
//
// Returns { radius, opacity } that animate by pulse group (same profiles as
// the old StatusDot component). Call per-frame via RAF driven by state.

import { useEffect, useRef, useState } from "react";

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

// ── Pulse profiles ───────────────────────────────────────────────────────────

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

const BASE_RADIUS = 4;

const PULSE_PARAMS: Record<PulseGroup, PulseParams> = {
  none:   { periodMs: 2000, rMin: BASE_RADIUS, rMax: BASE_RADIUS,     alphaMin: 1.0, alphaMax: 1.0 },
  slow:   { periodMs: 2000, rMin: BASE_RADIUS, rMax: BASE_RADIUS + 1, alphaMin: 0.7, alphaMax: 1.0 },
  fast:   { periodMs:  800, rMin: BASE_RADIUS, rMax: BASE_RADIUS + 2, alphaMin: 0.7, alphaMax: 1.0 },
  urgent: { periodMs:  500, rMin: BASE_RADIUS, rMax: BASE_RADIUS + 3, alphaMin: 0.7, alphaMax: 1.0 },
};

// ── Hook ─────────────────────────────────────────────────────────────────────

export interface StatusPulse {
  radius: number;
  opacity: number;
  fill: string;
}

export function useStatusPulse(status: string): StatusPulse {
  const group = pulseGroup(status);
  const params = PULSE_PARAMS[group];

  const phaseRef = useRef(0);
  const rafRef = useRef<number | null>(null);
  const lastTsRef = useRef<number | null>(null);
  const [, setTick] = useState(0);

  useEffect(() => {
    if (group === "none") {
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

  const t =
    group === "none"
      ? 0
      : (Math.sin((phaseRef.current / params.periodMs) * 2 * Math.PI) + 1) / 2;

  return {
    radius:  params.rMin + t * (params.rMax - params.rMin),
    opacity: params.alphaMin + t * (params.alphaMax - params.alphaMin),
    fill:    dotColor(status),
  };
}
