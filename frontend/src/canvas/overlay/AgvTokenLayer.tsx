// AgvTokenLayer - Live AGV motion tokens rendered in Konva stage-space.
//
// Stage 5 Track B changes:
//   - Subscribes to `agv_position` SocketIO events via the runtime slice's
//     `runtimePosition` field. Prefers runtimePosition when present (live
//     motion); falls back to authored actor.position (idle/initial state).
//   - Position tween: delegates to the existing spatialSlice tick() mechanism
//     (setAgvTarget in socketDriver → tick() interpolates). The layer reads
//     the already-interpolated pos from agvKinematics.
//   - Heading: uses runtimePosition.theta when available (precise atan2 from
//     backend); falls back to kinematics.heading (derived from travel direction).
//   - Vehicle shape upgraded to spec: 50×30 px rounded rect body + triangle
//     front pointer + drop shadow + actor_id label below.
//
// Stage 5 Track C changes (additive on top of Track B):
//   - Return path arrow: faint tapered line from current pos → authored origin
//     when phase === "returning". Drawn behind the body group.
//   - Idle breathing pulse: soft oscillating ring when idle at origin. Reuses
//     the existing RAF pulsePhase (no extra animation loop).
//   - Status dot: small circle beside the label with phase-keyed color.
//
// This layer is listening={false}. Drag/select are handled by RF nodes above.

import { useEffect, useRef, useState } from "react";
import { Layer, Group, Rect, Text, Arrow, Circle } from "react-konva";

import { ShuttleAgv } from "./agv/ShuttleAgv";
import { ForkliftAgv } from "./agv/ForkliftAgv";
import { TowAgv } from "./agv/TowAgv";

import type { ViewportTransform } from "./useViewportTransform";
import {
  useAgvKinematics,
  useActors,
  useAgvCargo,
  useActorRuntimeAll,
  useReplayMode,
  useReplayPositions,
} from "@/store/selectors";
import { useStore } from "@/store";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

// Shuttle body dims are the reference for transit halo size + label placement.
// Each silhouette component manages its own dims internally.
const BODY_W = 50;
const BODY_H = 30;

/** Default AGV color (spec: #16a085). */
const DEFAULT_AGV_COLOR = "#16a085";

/** Pulse speed for the cosmetic trail halo (legacy, kept for compat). */
const PULSE_SPEED = 2.4;
const TRAIL_OPACITY_IN_TRANSIT = 0.18;

/** Idle breathing ring — 2-second period. */
const IDLE_PULSE_SPEED = 1.0; // cycles per second (2 s period = 0.5 Hz × 2π)
const BODY_DIAGONAL = Math.sqrt((BODY_W / 2) ** 2 + (BODY_H / 2) ** 2);

/** Age threshold (ms) above which runtimePosition is considered stale. */
const STALE_THRESHOLD_MS = 1500;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function hexToRgba(hex: string, a: number): string {
  const r = parseInt(hex.slice(1, 3), 16) || 22;
  const g = parseInt(hex.slice(3, 5), 16) || 160;
  const b = parseInt(hex.slice(5, 7), 16) || 133;
  return `rgba(${r},${g},${b},${a.toFixed(3)})`;
}

// ---------------------------------------------------------------------------
// AgvTokenLayer
// ---------------------------------------------------------------------------

export function AgvTokenLayer({ tx }: { tx: ViewportTransform }) {
  const kinematics = useAgvKinematics();
  const actors = useActors();
  const agvCargo = useAgvCargo();
  const actorRuntime = useActorRuntimeAll();
  // Phase 5 Stage 8 Track C — replay position source (highest priority).
  const replayMode = useReplayMode();
  const replayPositions = useReplayPositions();
  const selectedActorId = useStore((s) => s.selectedActorId);

  // Per-AGV color fetched from /config (same as previous layer).
  const [agvColors, setAgvColors] = useState<Record<string, string>>({});
  useEffect(() => {
    fetch("/config")
      .then((r) => r.json())
      .then((cfg) => {
        const m: Record<string, string> = {};
        (cfg.warehouse?.agvs ?? []).forEach(
          (a: { id: string; color?: string }) => {
            m[a.id] = a.color ?? DEFAULT_AGV_COLOR;
          },
        );
        setAgvColors(m);
      })
      .catch(() => {});
  }, []);

  // Shared RAF for both transit halo pulse and idle breathing ring.
  // Track C reuses this loop — no second requestAnimationFrame.
  const [pulsePhase, setPulsePhase] = useState(0);
  const rafRef = useRef<number | null>(null);
  const lastTsRef = useRef<number | null>(null);
  const phaseRef = useRef(0);

  useEffect(() => {
    function animate(ts: number) {
      const dt = lastTsRef.current === null ? 0 : ts - lastTsRef.current;
      lastTsRef.current = ts;
      phaseRef.current =
        (phaseRef.current + (dt / 1000) * PULSE_SPEED * Math.PI * 2) %
        (Math.PI * 2);
      setPulsePhase(phaseRef.current);
      rafRef.current = requestAnimationFrame(animate);
    }
    rafRef.current = requestAnimationFrame(animate);
    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    };
  }, []);

  // Collect entries: prefer runtimePosition for position when kinematics has
  // no entry yet (first-sighting fallback to authored). Once agv_position
  // drives kinematics, the kin.pos is the tweened value to render.
  const entries = Object.entries(kinematics).filter(([, k]) => k.pos !== null);

  if (entries.length === 0) return null;

  const pulse = (Math.sin(pulsePhase) * 0.5 + 0.5) * 0.55 + 0.15; // 0.15..0.70

  // Idle breathing ring oscillates independently at IDLE_PULSE_SPEED Hz.
  // We derive a separate phase from pulsePhase, scaled by the ratio of speeds.
  const idlePhaseNorm = (pulsePhase / (Math.PI * 2)) * (IDLE_PULSE_SPEED / PULSE_SPEED);
  const idleSin = Math.sin(idlePhaseNorm * Math.PI * 2); // -1..1
  const idleT = idleSin * 0.5 + 0.5; // 0..1
  const idleRingRadius = BODY_DIAGONAL * (1 + idleT * 0.15); // diagonal .. diagonal × 1.15
  const idleRingAlpha = 0.05 + idleT * 0.10; // 0.05..0.15

  const now = Date.now();

  return (
    <Layer
      listening={false}
      x={tx.x}
      y={tx.y}
      scaleX={tx.zoom}
      scaleY={tx.zoom}
    >
      {entries.map(([id, k]) => {
        // Phase 5 Stage 8 Track C — position-resolution seam.
        // Priority: replayPositions (highest) > runtimeKinematics > authored.
        // In replay mode, snap to historical position (no tween).
        const replayPos = replayMode ? (replayPositions[id] ?? null) : null;
        const pos = replayPos !== null ? { x: replayPos.x, y: replayPos.y } : k.pos;
        const { target } = k;
        // In replay mode there is no live target; suppress transit halo.
        const inTransit = replayPos === null && target !== null;

        // Heading: replay > runtimePosition.theta > kinematics heading.
        const rtp = actorRuntime[id]?.runtimePosition;
        const theta =
          replayPos !== null
            ? replayPos.theta
            : rtp != null
              ? rtp.theta
              : k.heading;
        const headingDeg = (theta * 180) / Math.PI;

        const actor = actors[id];
        const agvColor =
          agvColors[id] ??
          (actor as (typeof actor & { color?: string }) | undefined)?.color ??
          DEFAULT_AGV_COLOR;
        const isSelected = id === selectedActorId;
        const trailOpacity = inTransit ? pulse * TRAIL_OPACITY_IN_TRANSIT : 0;
        const cargo = agvCargo[id] ?? null;

        // Label text: full actor id (e.g. "agv-1").
        const labelText = id;

        // --- Track C: phase-based status for dot + return arrow + idle pulse ---
        const rt = actorRuntime[id];
        // `phase` holds the raw backend status string ("returning", "moving", etc.)
        const phase = rt?.phase ?? "";

        // Return path arrow: authored origin (canvas drop position).
        const origin = actor?.position ?? null;
        const isReturning = phase === "returning";

        // Idle pulse: shown when phase is idle AND runtimePosition is stale/absent.
        const rtpAge = rtp != null ? now - rtp.ts * 1000 : Infinity;
        const isIdleAtOrigin =
          (phase === "idle" || rt?.status === "idle") &&
          rtpAge > STALE_THRESHOLD_MS;

        return (
          <Group key={id} x={pos.x} y={pos.y}>

            {/* ── Track C: Return path arrow ──────────────────────────────────
                Rendered first (lowest z) so it's behind everything else.
                Visible only when phase === "returning". Origin coords are in
                stage space relative to the layer's own transform, so we
                translate from the group's current position (pos) to origin. */}
            {isReturning && origin !== null && (
              <Arrow
                points={[
                  0,
                  0,
                  origin.x - pos.x,
                  origin.y - pos.y,
                ]}
                pointerLength={8}
                pointerWidth={6}
                fill={hexToRgba(agvColor, 0.4)}
                stroke={hexToRgba(agvColor, 0.4)}
                strokeWidth={1.5}
                dash={[6, 4]}
                opacity={1}
              />
            )}

            {/* ── Track C: Idle breathing ring ────────────────────────────────
                Drawn behind the body group. Subtle — low opacity 0.05→0.15. */}
            {isIdleAtOrigin && (
              <Circle
                x={0}
                y={0}
                radius={idleRingRadius}
                fill={hexToRgba(agvColor, idleRingAlpha)}
                listening={false}
              />
            )}

            {/* Transit halo — subtle radial hint when in motion.
                Rendered outside the silhouette group so it's always behind
                the body regardless of subtype dimensions. */}
            {inTransit && (
              <Group rotation={headingDeg}>
                <Rect
                  x={-BODY_W / 2 - 4}
                  y={-BODY_H / 2 - 4}
                  width={BODY_W + 8}
                  height={BODY_H + 8}
                  cornerRadius={10}
                  fill={hexToRgba(agvColor, trailOpacity)}
                />
              </Group>
            )}

            {/* ── Per-kind silhouette dispatch ─────────────────────────────────
                Each component renders its own body + status dot in the group's
                local coordinate space (centred at 0,0). The label stays here
                so it's always legible regardless of heading. */}
            {(() => {
              const subtype = actor?.subtype ?? "shuttle";
              const silhouetteProps = {
                x: 0,
                y: 0,
                theta,
                color: agvColor,
                isSelected,
                carried: cargo !== null,
                status: phase,
              };
              if (subtype === "forklift") {
                return <ForkliftAgv {...silhouetteProps} />;
              }
              if (subtype === "tow") {
                return <TowAgv {...silhouetteProps} />;
              }
              // Default: "shuttle"
              return <ShuttleAgv {...silhouetteProps} />;
            })()}

            {/* Actor id label — rendered in world space (not rotated with vehicle)
                so it's always legible regardless of heading direction. */}
            <Text
              x={-BODY_W / 2}
              y={BODY_H / 2 + 5}
              width={BODY_W}
              align="center"
              text={labelText}
              fontSize={9}
              fill="rgba(255,255,255,0.75)"
              fontStyle="500"
            />
          </Group>
        );
      })}
    </Layer>
  );
}
