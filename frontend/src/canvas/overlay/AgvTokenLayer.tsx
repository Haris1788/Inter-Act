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
import { Layer, Group, Rect, Circle, Line } from "react-konva";

import type { ViewportTransform } from "./useViewportTransform";
import { useAgvKinematics, useActors, useAgvCargo } from "@/store/selectors";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const TOKEN_W = 22;
const TOKEN_H = 16;
const TRAIL_R = 24;
const TETHER_OPACITY = 0.18;
const PULSE_SPEED = 2.4;

function hexToRgba(hex: string, a: number): string {
  const r = parseInt(hex.slice(1, 3), 16) || 24;
  const g = parseInt(hex.slice(3, 5), 16) || 24;
  const b = parseInt(hex.slice(5, 7), 16) || 27;
  return `rgba(${r},${g},${b},${a.toFixed(3)})`;
}

// ---------------------------------------------------------------------------
// AgvTokenLayer
// ---------------------------------------------------------------------------

export function AgvTokenLayer({ tx }: { tx: ViewportTransform }) {
  const kinematics = useAgvKinematics();
  const actors = useActors();
  const agvCargo = useAgvCargo();
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
  }, []);

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

  const entries = Object.entries(kinematics).filter(([, k]) => k.pos !== null);

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

        const agvColor = agvColors[id] ?? "#888";
        const bodyFill = inTransit ? agvColor : hexToRgba(agvColor, 0.85);
        const strokeColor = "rgba(0,0,0,0.35)";
        const strokeWidth = inTransit ? 1.4 : 1.0;
        const trailOpacity = inTransit ? pulse * 0.45 : 0;
        const cargo = agvCargo[id] ?? null;

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

              {/* Package on the cargo bed - clearly visible when carrying */}
              {cargo !== null && (
                <>
                  <Rect
                    x={-TOKEN_W * 0.3}
                    y={-TOKEN_H * 0.36}
                    width={TOKEN_W * 0.6}
                    height={TOKEN_H * 0.72}
                    cornerRadius={1.5}
                    fill="#d9a566"
                    stroke="#7a4a1e"
                    strokeWidth={1}
                  />
                  <Line
                    points={[0, -TOKEN_H * 0.36, 0, TOKEN_H * 0.36]}
                    stroke="#7a4a1e"
                    strokeWidth={0.8}
                    opacity={0.7}
                  />
                </>
              )}
            </Group>
          </Group>
        );
      })}
    </Layer>
  );
}
