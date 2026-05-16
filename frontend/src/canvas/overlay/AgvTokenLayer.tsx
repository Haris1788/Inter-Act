// AgvTokenLayer — Live AGV motion tokens rendered in Konva stage-space.
//
// For each AGV in useAgvKinematics() with a non-null pos, draws a matte
// industrial marker at that position, locked to React Flow pan/zoom via the
// same useViewportTransform() transform that the other Layers receive.
//
// Rendering rules:
//   - Token: rounded rect 18×14 px (stage units), tokens.cta stroke, subtle
//     warm fill, heading tick line pointing in the direction of travel.
//   - In-transit (target non-null): soft radial trail halo; pulse phase driven
//     by a dedicated rAF (cosmetic only — position integration is in the store).
//   - Settled (target null): no trail; reduced opacity on heading tick.
//   - Tether: thin low-opacity line from token centre to the AGV's RF home
//     card centre (actor.position + size/2) while in transit.
//
// This layer is listening={false} and does NOT integrate positions — it only
// reads current store state each render frame.

import { useEffect, useRef, useState } from "react";
import { Layer, Group, Rect, Circle, Line } from "react-konva";

import type { ViewportTransform } from "./useViewportTransform";
import { useAgvKinematics, useActors } from "@/store/selectors";
import { tokens } from "@/lib/tokens";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const TOKEN_W = 18; // stage px
const TOKEN_H = 14;
const TICK_LEN = 8; // heading indicator length
const TRAIL_R = 22; // radial trail radius (in-transit halo)
const TETHER_OPACITY = 0.18;
const PULSE_SPEED = 2.4; // full cycle per second

// ---------------------------------------------------------------------------
// AgvTokenLayer
// ---------------------------------------------------------------------------

export function AgvTokenLayer({ tx }: { tx: ViewportTransform }) {
  const kinematics = useAgvKinematics();
  const actors = useActors();

  // Cosmetic pulse phase — only used for the trail glow, harmless if no AGVs
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
      phaseRef.current = (phaseRef.current + (dt / 1000) * PULSE_SPEED * Math.PI * 2) % (Math.PI * 2);
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
    <Layer listening={false} x={tx.x} y={tx.y} scaleX={tx.zoom} scaleY={tx.zoom}>
      {entries.map(([id, k]) => {
        const { pos, heading, target } = k;
        const inTransit = target !== null;

        // Home card centre for tether
        const actor = actors[id];
        const homeX = actor ? actor.position.x + actor.size.w / 2 : null;
        const homeY = actor ? actor.position.y + actor.size.h / 2 : null;

        // Heading tick direction
        const tickDx = Math.cos(heading) * TICK_LEN;
        const tickDy = Math.sin(heading) * TICK_LEN;

        // Token colours — matte warm-industrial palette
        const fillColor = inTransit ? "rgba(24,24,27,0.07)" : "rgba(24,24,27,0.04)";
        const strokeColor = tokens.cta; // #18181B — same as arm halos
        const strokeWidth = inTransit ? 1.2 : 0.9;
        const tickOpacity = inTransit ? 0.65 : 0.28;

        // Trail halo opacity — pulsed when in transit, hidden when settled
        const trailOpacity = inTransit ? pulse * 0.45 : 0;

        return (
          <Group key={id} x={pos.x} y={pos.y}>
            {/* Tether line to home card — only while in transit */}
            {inTransit && homeX !== null && homeY !== null && (
              <Line
                points={[0, 0, homeX - pos.x, homeY - pos.y]}
                stroke={tokens.ink[3]}
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
                  `rgba(24,24,27,${(trailOpacity * 0.6).toFixed(3)})`,
                  1,
                  "rgba(24,24,27,0)",
                ]}
              />
            )}

            {/* AGV token body — rounded rect centred at (0,0) */}
            <Rect
              x={-TOKEN_W / 2}
              y={-TOKEN_H / 2}
              width={TOKEN_W}
              height={TOKEN_H}
              cornerRadius={4}
              fill={fillColor}
              stroke={strokeColor}
              strokeWidth={strokeWidth}
            />

            {/* Heading indicator tick */}
            <Line
              points={[0, 0, tickDx, tickDy]}
              stroke={strokeColor}
              strokeWidth={1}
              lineCap="round"
              opacity={tickOpacity}
            />
          </Group>
        );
      })}
    </Layer>
  );
}
