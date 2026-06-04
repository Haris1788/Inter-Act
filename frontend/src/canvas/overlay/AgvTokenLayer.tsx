// AgvTokenLayer - Live AGV motion tokens rendered in Konva stage-space.
//
// For each AGV in useAgvKinematics() with a non-null pos, draws a matte
// industrial marker at that position, locked to React Flow pan/zoom via the
// same useViewportTransform() transform that the other Layers receive.
//
// Rendering rules:
//   - Token: rounded rect 18×14 px (stage units), tokens.cta stroke, subtle
//     warm fill, heading tick line pointing in the direction of travel.
//   - In-transit (target non-null): soft radial trail halo; pulse phase driven
//     by a dedicated rAF (cosmetic only - position integration is in the store).
//   - Settled (target null): no trail; reduced opacity on heading tick.
//   - Tether: thin low-opacity line from token centre to the AGV's RF home
//     card centre (actor.position + size/2) while in transit.
//
// This layer is listening={false} and does NOT integrate positions - it only
// reads current store state each render frame.

import { useEffect, useRef, useState } from "react";
import { Layer, Group, Rect, Circle, Line, Text } from "react-konva";

import type { ViewportTransform } from "./useViewportTransform";
import type { AgvKinematics } from "@/store/types";
import { useAgvKinematics, useActors, useAgvCargo, useDeployedAt } from "@/store/selectors";
import { useStore } from "@/store";
import { useStatusPulse } from "./agv/useStatusPulse";

// Authoring-time parity: authored AGVs (in topology.actors, no backend /config
// colour) get a palette colour so the chassis + bay read like the seeded fleet
// instead of the "#888" fallback grey. Exported so WarehouseGridLayer paints the
// authored bay outline in the same colour.
const AUTHORED_AGV_COLORS = ["#2980b9", "#27ae60", "#f1c40f", "#8e44ad", "#e67e22"];
export function authoredAgvColor(id: string): string {
  const n = parseInt(id.replace(/\D/g, ""), 10) || 1;
  return AUTHORED_AGV_COLORS[(n - 1) % AUTHORED_AGV_COLORS.length];
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const TOKEN_W = 22;
const TOKEN_H = 16;
const TRAIL_R = 24;
const TETHER_OPACITY = 0.18;
const PULSE_SPEED = 2.4;
// US 34.1 (Sneh-port 5c23991): inline status-dot + label rendered below the
// chassis. The dot pulses by state (fast moving / urgent fault / static
// stopped); the label is black for legibility against the light canvas.
const LABEL_FONT_SIZE = 10;
const LABEL_DOT_GAP = 4;
const LABEL_Y_OFFSET = TOKEN_H / 2 + 8;

function hexToRgba(hex: string, a: number): string {
  const r = parseInt(hex.slice(1, 3), 16) || 24;
  const g = parseInt(hex.slice(3, 5), 16) || 24;
  const b = parseInt(hex.slice(5, 7), 16) || 27;
  return `rgba(${r},${g},${b},${a.toFixed(3)})`;
}

// US 34.1 (Sneh-port 5c23991): inline status dot + AGV id label, rendered
// in WORLD SPACE (outside the chassis rotation Group) so the label always
// reads horizontally regardless of vehicle heading. The dot pulses by
// state via useStatusPulse, the label is black on light canvas.
function AgvStatusLabel({ id }: { id: string }) {
  // Pull the AGV's phase from runtime — phase is the raw backend status
  // ("moving" / "loading" / "faulted" / "idle" / ...). The pulse hook
  // returns "none" for unknown so missing runtime falls back to a still dot.
  const phase = useStore((s) => s.actorRuntime[id]?.phase ?? "stopped");
  const pulse = useStatusPulse(phase);
  // Centre the (dot + " agv-N") line under the chassis. Use approx text width
  // to compute the offset — Konva Text doesn't auto-centre across siblings.
  const textPx = id.length * (LABEL_FONT_SIZE * 0.55);
  const totalW = pulse.radius * 2 + LABEL_DOT_GAP + textPx;
  const startX = -totalW / 2;
  return (
    <Group y={LABEL_Y_OFFSET}>
      <Circle
        x={startX + pulse.radius}
        y={0}
        radius={pulse.radius}
        fill={pulse.fill}
        opacity={pulse.opacity}
      />
      <Text
        x={startX + pulse.radius * 2 + LABEL_DOT_GAP}
        y={-LABEL_FONT_SIZE / 2}
        text={id}
        fontSize={LABEL_FONT_SIZE}
        fill="rgba(0,0,0,0.85)"
        fontStyle="600"
      />
    </Group>
  );
}

// ---------------------------------------------------------------------------
// AgvTokenLayer
// ---------------------------------------------------------------------------

export function AgvTokenLayer({ tx }: { tx: ViewportTransform }) {
  const kinematics = useAgvKinematics();
  const actors = useActors();
  const agvCargo = useAgvCargo();
  const deployedAt = useDeployedAt();
  const [agvColors, setAgvColors] = useState<Record<string, string>>({});

  useEffect(() => {
    fetch("/config")
      .then((r) => r.json())
      .then((cfg) => {
        const m: Record<string, string> = {};
        (cfg.warehouse?.agvs ?? []).forEach(
          (a: { id: string; color?: string }) => {
            m[a.id] = a.color ?? "#888";
          },
        );
        setAgvColors(m);
      })
      .catch(() => {});
  // deployedAt: re-fetch so authored AGV tokens use the runtime color from
  // warehouse.agvs instead of the authoredAgvColor() fallback.
  }, [deployedAt]);

  // Cosmetic pulse phase - only used for the trail glow, harmless if no AGVs
  // are in transit. A single shared phase is fine; all tokens pulse together
  // which reads as a system heartbeat rather than per-unit animation.
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

  // Phase 7 Stage D fix: tokens are gated to AGV actors in the topology store —
  // the canvas is actor-driven, so delete-all leaves a blank canvas. An AGV
  // actor renders at its live interpolated pos (kinematics from agv_update) when
  // available, else a STATIC chassis at its authored card centre (target:null →
  // settled, no trail). Runtime kinematics for an AGV NOT in the topology (e.g.
  // a seeded fleet AGV with no actor) is no longer drawn — no orphan token.
  const entries: [string, AgvKinematics][] = Object.values(actors)
    .filter((a) => a.kind === "agv")
    .map((a) => {
      const k = kinematics[a.id];
      // First-authored-AGV fix: a freshly-authored AGV must render at its
      // AUTHORED position, not adopt a live runtime AGV's kinematics by id
      // coincidence (e.g. dropping the first AGV → id "veh-1" which already
      // exists in the seeded fleet). That decoupled the visible Konva token
      // from its RF interaction shell (still at the authored position), making
      // the token unselectable/unmovable/undeletable. Kinematics applies only
      // to non-authored (seeded/deployed) AGVs, or to an authored AGV that is
      // genuinely in transit (target set → actually running post-deploy).
      const useKin = !!k && k.pos !== null && (!a.authored || k.target !== null);
      if (useKin) return [a.id, k];
      return [
        a.id,
        {
          pos: { x: a.position.x + a.size.w / 2, y: a.position.y + a.size.h / 2 },
          heading: 0,
          speed: 0,
          target: null,
        },
      ];
    });

  if (entries.length === 0) return null;

  // Pulse multiplier: 0..1 sine, biased to spend more time near 0 (calm)
  const pulse = (Math.sin(pulsePhase) * 0.5 + 0.5) * 0.55 + 0.15; // 0.15..0.70

  return (
    <Layer
      listening={false}
      x={tx.x}
      y={tx.y}
      scaleX={tx.zoom}
      scaleY={tx.zoom}
    >
      {entries.map(([id, k]) => {
        const { pos, heading, target } = k;
        const inTransit = target !== null;

        // Home card centre for tether
        const actor = actors[id];
        const homeX = actor ? actor.position.x + actor.size.w / 2 : null;
        const homeY = actor ? actor.position.y + actor.size.h / 2 : null;

        // Vehicle faces its travel direction (forward = +x in local space).
        const headingDeg = (heading * 180) / Math.PI;

        const agvColor = agvColors[id] ?? authoredAgvColor(id);
        const bodyFill = agvColor;          // always fully opaque — was 0.85 alpha when idle
        const strokeColor = "rgba(0,0,0,0.75)";  // darker outline so AGV is clearly visible against route
        const strokeWidth = 2.0;
        const trailOpacity = inTransit ? pulse * 0.45 : 0;
        // cargo is now rendered by PayloadLayer using object_update events.
        void agvCargo;

        return (
          <Group key={id} x={pos.x} y={pos.y}>
            {/* Tether line to home card - only while in transit */}
            {inTransit && homeX !== null && homeY !== null && (
              <Line
                points={[0, 0, homeX - pos.x, homeY - pos.y]}
                stroke={agvColor}
                strokeWidth={0.7}
                dash={[3, 5]}
                opacity={TETHER_OPACITY}
              />
            )}

            {/* Radial trail halo (in-transit pulse) */}
            {inTransit && (
              <Circle
                x={0}
                y={0}
                radius={TRAIL_R}
                fillRadialGradientStartPoint={{ x: 0, y: 0 }}
                fillRadialGradientEndPoint={{ x: 0, y: 0 }}
                fillRadialGradientStartRadius={0}
                fillRadialGradientEndRadius={TRAIL_R}
                fillRadialGradientColorStops={[
                  0,
                  hexToRgba(agvColor, trailOpacity * 0.5),
                  1,
                  hexToRgba(agvColor, 0),
                ]}
              />
            )}

            {/* Vehicle, rotated to face its direction of travel */}
            <Group rotation={headingDeg}>
              {/* Four wheels (dark, straddling the chassis edges) */}
              {[
                [-TOKEN_W * 0.34, -TOKEN_H / 2 - 1.5],
                [TOKEN_W * 0.1, -TOKEN_H / 2 - 1.5],
                [-TOKEN_W * 0.34, TOKEN_H / 2 - 2.5],
                [TOKEN_W * 0.1, TOKEN_H / 2 - 2.5],
              ].map(([wx, wy], wi) => (
                <Rect
                  key={wi}
                  x={wx}
                  y={wy}
                  width={TOKEN_W * 0.24}
                  height={4}
                  cornerRadius={1.5}
                  fill="rgba(20,20,24,0.85)"
                />
              ))}

              {/* Chassis */}
              <Rect
                x={-TOKEN_W / 2}
                y={-TOKEN_H / 2}
                width={TOKEN_W}
                height={TOKEN_H}
                cornerRadius={3}
                fill={bodyFill}
                stroke={strokeColor}
                strokeWidth={strokeWidth}
              />

              {/* Front bumper / headlight strip (forward = +x) */}
              <Rect
                x={TOKEN_W / 2 - 3}
                y={-TOKEN_H * 0.3}
                width={3}
                height={TOKEN_H * 0.6}
                cornerRadius={1}
                fill="rgba(255,255,255,0.75)"
              />

              {/* Sneh-port: package render moved to PayloadLayer. PayloadLayer
                  resolves agv-carrier payloads to this AGV's live interpolated
                  position, so the carried package always rides the chassis
                  with no duplicate render in the table/arm slots. */}
            </Group>

            {/* US 34.1: status dot + label, rendered OUTSIDE the rotation
                so the line stays horizontal regardless of heading. */}
            <AgvStatusLabel id={id} />
          </Group>
        );
      })}
    </Layer>
  );
}
