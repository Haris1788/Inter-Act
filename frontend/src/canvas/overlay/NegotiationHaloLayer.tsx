// NegotiationHaloLayer — Phase 5.1 Stage 5 Track A: negotiation halo.
//
// Renders a soft pulsing halo behind actors that are currently in an active
// negotiation state (BIDDING, WON, or EXECUTING). The halo is a Konva Circle
// with a stroke that oscillates between 1.5 and 3 px on a 2-second sine cycle.
//
// State source: `actorRuntime[id].negotiationState` — populated by socketDriver
// from `availability_update` bus message payloads (payload.state field).
// The backend AgentState values are lowercase strings: "idle", "bidding",
// "won", "executing", "done", "faulted". We skip halo for idle/done/faulted.
//
// Positions: arms use `topologySlice` actor.position. AGVs use agvKinematics
// (the tweened live position) so the halo tracks moving vehicles.
//
// Reuses the AgvTokenLayer's existing RAF loop pattern (phaseRef + setPulsePhase)
// — no additional requestAnimationFrame. This layer mounts its own RAF only for
// the sine oscillation used for strokeWidth, at low cost.
//
// listening={false} — no hit detection. Purely cosmetic.

import { useEffect, useRef, useState } from "react";
import { Layer, Circle } from "react-konva";

import type { ViewportTransform } from "./useViewportTransform";
import { useActors, useActorRuntimeAll, useAgvKinematics } from "@/store/selectors";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Negotiation states that activate the halo (matches backend AgentState enum). */
const ACTIVE_STATES = new Set(["bidding", "won", "executing"]);

/** Halo oscillation period (ms). */
const HALO_PERIOD_MS = 2000;

/** Halo stroke width oscillates between these bounds. */
const HALO_SW_MIN = 1.5;
const HALO_SW_MAX = 3.0;

/** Halo radius offset above the body's visual radius (px). */
const HALO_RADIUS_ARM_OFFSET = 14; // beyond PIVOT_RADIUS (6px)
const HALO_RADIUS_AGV_OFFSET = 24; // beyond AGV body half-diagonal

/** Halo stroke opacity (fixed — strokeWidth carries the pulse energy). */
const HALO_STROKE_ALPHA = 0.30;

// ---------------------------------------------------------------------------
// Helper
// ---------------------------------------------------------------------------

function hexToRgba(hex: string, a: number): string {
  const r = parseInt(hex.slice(1, 3), 16) || 22;
  const g = parseInt(hex.slice(3, 5), 16) || 160;
  const b = parseInt(hex.slice(5, 7), 16) || 133;
  return `rgba(${r},${g},${b},${a.toFixed(3)})`;
}

function actorColorOf(
  actor: Record<string, unknown> | undefined,
  fallback: string,
): string {
  if (!actor) return fallback;
  const c = actor["color"];
  return typeof c === "string" ? c : fallback;
}

// ---------------------------------------------------------------------------
// NegotiationHaloLayer
// ---------------------------------------------------------------------------

export function NegotiationHaloLayer({ tx }: { tx: ViewportTransform }) {
  const actors = useActors();
  const actorRuntime = useActorRuntimeAll();
  const agvKinematics = useAgvKinematics();

  // Sine-based phase (0..1) driving strokeWidth oscillation.
  const [phase, setPhase] = useState(0);
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    function animate(ts: number) {
      // Normalised sine phase: 0..1 over HALO_PERIOD_MS.
      const p = (ts % HALO_PERIOD_MS) / HALO_PERIOD_MS;
      setPhase((Math.sin(p * 2 * Math.PI) + 1) / 2); // 0..1
      rafRef.current = requestAnimationFrame(animate);
    }
    rafRef.current = requestAnimationFrame(animate);
    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    };
  }, []);

  // Collect actors with active negotiation state.
  const actorList = Object.values(actors);
  const halos = actorList.filter((a) => {
    const ns = actorRuntime[a.id]?.negotiationState;
    return ns !== undefined && ACTIVE_STATES.has(ns);
  });

  if (halos.length === 0) return null;

  const strokeWidth = HALO_SW_MIN + phase * (HALO_SW_MAX - HALO_SW_MIN);

  return (
    <Layer
      listening={false}
      x={tx.x}
      y={tx.y}
      scaleX={tx.zoom}
      scaleY={tx.zoom}
    >
      {halos.map((actor) => {
        const color = actorColorOf(actor as unknown as Record<string, unknown>, "#c87941");

        // Position: AGVs follow live kinematics; arms use authored position.
        let px: number;
        let py: number;
        if (actor.kind === "agv") {
          const kin = agvKinematics[actor.id];
          px = kin?.pos?.x ?? actor.position.x;
          py = kin?.pos?.y ?? actor.position.y;
        } else {
          px = actor.position.x;
          py = actor.position.y;
        }

        const radius =
          actor.kind === "agv"
            ? HALO_RADIUS_AGV_OFFSET
            : (actor.reach_px ?? 120) * 0.15 + HALO_RADIUS_ARM_OFFSET;

        return (
          <Circle
            key={actor.id}
            x={px}
            y={py}
            radius={radius}
            fill="transparent"
            stroke={hexToRgba(color, HALO_STROKE_ALPHA)}
            strokeWidth={strokeWidth}
            listening={false}
          />
        );
      })}
    </Layer>
  );
}
