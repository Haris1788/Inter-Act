// PayloadLayer — Phase-6 visible object lifecycle.
//
// Reads `payloads` slice (driven by PAYLOAD_SPAWN / PAYLOAD_TRANSFER bus events).
// Resolves each payload's draw position from its carrier:
//
//   carrier = null               → use payload.pos (spawn position, last-known).
//   carrier = "zone:src@<task>"  → use payload.pos (spawn snapshot).
//   carrier = "zone:dst@<task>"  → use payload.pos (last arm pos at delivery).
//   carrier = arm-N              → arm actor's canvas position.
//   carrier = agv-N              → AGV's live kinematics pos (interpolated).
//
// When carrier resolution updates the position, we also update payload.pos so
// the next transfer to a zone keeps a sensible last-known point.
//
// Locked to the same RF viewport transform as the other Konva layers.

import { Layer, Circle } from "react-konva";
import {
  useActors,
  useAgvKinematics,
  usePayloads,
} from "@/store/selectors";
import type { ViewportTransform } from "./useViewportTransform";

const RADIUS = 7;
const STROKE = "rgba(40, 30, 10, 0.85)";

interface Props {
  tx: ViewportTransform;
}

export function PayloadLayer({ tx }: Props) {
  const payloads = usePayloads();
  const actors = useActors();
  const kin = useAgvKinematics();

  const items: { id: string; x: number; y: number; color: string }[] = [];
  for (const [id, p] of Object.entries(payloads)) {
    let pos = p.pos;
    if (p.carrier && !p.carrier.startsWith("zone:")) {
      const carrierKin = kin[p.carrier];
      const actor = actors[p.carrier];
      pos = carrierKin?.pos ?? actor?.position ?? pos;
    }
    if (!pos) continue;
    items.push({ id, x: pos.x, y: pos.y, color: p.color });
  }

  if (items.length === 0) return null;

  return (
    <Layer
      listening={false}
      x={tx.x}
      y={tx.y}
      scaleX={tx.zoom}
      scaleY={tx.zoom}
    >
      {items.map((it) => (
        <Circle
          key={it.id}
          x={it.x}
          y={it.y}
          radius={RADIUS}
          fill={it.color}
          stroke={STROKE}
          strokeWidth={1.2}
          shadowColor="rgba(0,0,0,0.35)"
          shadowBlur={4}
          shadowOffset={{ x: 0, y: 1 }}
        />
      ))}
    </Layer>
  );
}
