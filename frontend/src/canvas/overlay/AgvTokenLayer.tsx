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
// Phase 5.1 Fix Pass — AGV drop invisibility fix:
//   Newly dropped AGVs exist in the topology `actors` slice but have no
//   kinematics entry (no backend socket events yet). The layer now builds a
//   merged render list: kinematics entries (live/tween) UNION authored AGV actors
//   not yet in kinematics (idle at their authored centre). The authored centre
//   is actor.position + size/2, matching the RF node centroid.
//   A 300 ms scale-in animation acknowledges the drop immediately.
//
// This layer is listening={false}. Drag/select are handled by RF nodes above.

import { useEffect, useMemo, useRef, useState } from "react";
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

  // --- Phase 5.1 Fix: merge authored AGV actors + kinematics into one list ---
  //
  // "entries" is an array of [id, kinematicsOrNull] tuples covering every AGV
  // that should be rendered:
  //   1. Kinematics entries (live tweened positions from backend socket events).
  //   2. Authored actors with kind === "agv" that have NO kinematics yet
  //      (newly dropped, not yet driven by backend). Their position is the
  //      authored node centre (position + size/2).
  //
  // The merge ensures a drop ALWAYS produces an immediate visible token, even
  // before the first agv_position event arrives.
  const entries = useMemo(() => {
    type KinOrNull = (typeof kinematics)[string] | null;
    const result: [string, KinOrNull][] = [];
    const seen = new Set<string>();
    // 1. Kinematics (live/tween — preferred source for position).
    for (const [id, k] of Object.entries(kinematics)) {
      if (k.pos !== null) {
        result.push([id, k]);
        seen.add(id);
      }
    }
    // 2. Authored AGV actors without a kinematics entry yet (idle/dropped).
    for (const [id, actor] of Object.entries(actors)) {
      if (actor.kind !== "agv" || seen.has(id)) continue;
      // Centre of the RF node box → Konva world-space position.
      const cx = actor.position.x + actor.size.w / 2;
      const cy = actor.position.y + actor.size.h / 2;
      result.push([id, {
        pos: { x: cx, y: cy },
        heading: 0,
        speed: 220,
        target: null,
        origin: null,
      }]);
    }
    return result;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kinematics, actors]);

  // Track when each AGV id first appeared so we can animate a scale-in.
  // Keyed by id → timestamp (ms). New ids get the current time; existing ids
  // keep their original entry so the animation plays only on the first render.
  const firstSeenRef = useRef<Record<string, number>>({});
  const nowMs = Date.now();
  for (const [id] of entries) {
    if (firstSeenRef.current[id] === undefined) {
      firstSeenRef.current[id] = nowMs;
    }
  }

  if (entries.length === 0) return null;

  const pulse = (Math.sin(pulsePhase) * 0.5 + 0.5) * 0.55 + 0.15; // 0.15..0.70

  // Idle breathing ring oscillates independently at IDLE_PULSE_SPEED Hz.
  // We derive a separate phase from pulsePhase, scaled by the ratio of speeds.
  const idlePhaseNorm = (pulsePhase / (Math.PI * 2)) * (IDLE_PULSE_SPEED / PULSE_SPEED);
  const idleSin = Math.sin(idlePhaseNorm * Math.PI * 2); // -1..1
  const idleT = idleSin * 0.5 + 0.5; // 0..1
  const idleRingRadius = BODY_DIAGONAL * (1 + idleT * 0.15); // diagonal .. diagonal × 1.15
  const idleRingAlpha = 0.05 + idleT * 0.10; // 0.05..0.15

  // nowMs used for both idle-pulse staleness check and drop-fade age.
  const now = nowMs;

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
        const pos = replayPos !== null ? { x: replayPos.x, y: replayPos.y } : (k?.pos ?? { x: 0, y: 0 });
        const target = k?.target ?? null;
        // In replay mode there is no live target; suppress transit halo.
        const inTransit = replayPos === null && target !== null;

        // Phase 5.1 Fix: scale-in fade for newly dropped tokens (300 ms).
        // Existing AGVs (first seen > 300 ms ago) render at full opacity.
        const ageMs = nowMs - (firstSeenRef.current[id] ?? nowMs);
        const FADE_MS = 300;
        const dropOpacity = Math.min(1, ageMs / FADE_MS);
        const dropScale = 0.5 + 0.5 * Math.min(1, ageMs / FADE_MS);

        // Heading: replay > runtimePosition.theta > kinematics heading.
        const rtp = actorRuntime[id]?.runtimePosition;
        const theta =
          replayPos !== null
            ? replayPos.theta
            : rtp != null
              ? rtp.theta
              : (k?.heading ?? 0);
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
          <Group key={id} x={pos.x} y={pos.y} opacity={dropOpacity} scaleX={dropScale} scaleY={dropScale}>

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
                so it's always legible regardless of heading direction.
                Black for legibility against the light canvas background. */}
            <Text
              x={-BODY_W / 2}
              y={BODY_H / 2 + 5}
              width={BODY_W}
              align="center"
              text={labelText}
              fontSize={9}
              fill="rgba(0,0,0,0.85)"
              fontStyle="600"
            />
          </Group>
        );
      })}
    </Layer>
  );
}
