// ZoneMarkerLayer — Konva physical-world markers for zone actors (kind="zone").
//
// Renders one labeled circle per zone actor:
//   · Source (subtype="source"): green-ish circle, radius 40 px, "IN" label.
//   · Sink   (subtype="sink"):   red-ish  circle, radius 40 px, "OUT" label.
//
// Positioned at actor.position (top-left of the 100×100 size box) + 50px offset
// to center the circle inside the box. Locked to RF viewport transform.
// listening={false} — React Flow handles drag/selection on the RF node.

import React from "react";
import { Layer, Circle, Text } from "react-konva";
import { useActors } from "@/store/selectors";
import type { ViewportTransform } from "./useViewportTransform";

const ZONE_RADIUS = 40;

const SOURCE_FILL = "rgba(80, 180, 100, 0.18)";
const SOURCE_STROKE = "rgba(60, 160, 80, 0.70)";
const SOURCE_LABEL_COLOR = "rgba(40, 130, 60, 0.90)";

const SINK_FILL = "rgba(200, 70, 70, 0.15)";
const SINK_STROKE = "rgba(180, 50, 50, 0.65)";
const SINK_LABEL_COLOR = "rgba(160, 40, 40, 0.90)";

interface Props {
  tx: ViewportTransform;
}

export function ZoneMarkerLayer({ tx }: Props) {
  const actors = useActors();
  const zones = Object.values(actors).filter((a) => a.kind === "zone");

  if (zones.length === 0) return null;

  return (
    <Layer
      listening={false}
      x={tx.x}
      y={tx.y}
      scaleX={tx.zoom}
      scaleY={tx.zoom}
    >
      {zones.map((actor) => {
        const cx = actor.position.x + actor.size.w / 2;
        const cy = actor.position.y + actor.size.h / 2;
        const isSource = actor.subtype === "source";
        const fill = isSource ? SOURCE_FILL : SINK_FILL;
        const stroke = isSource ? SOURCE_STROKE : SINK_STROKE;
        const labelColor = isSource ? SOURCE_LABEL_COLOR : SINK_LABEL_COLOR;
        const label = isSource ? "IN" : "OUT";

        return (
          <React.Fragment key={actor.id}>
            <Circle
              x={cx}
              y={cy}
              radius={ZONE_RADIUS}
              fill={fill}
              stroke={stroke}
              strokeWidth={1.5}
              dash={[5, 4]}
            />
            <Text
              x={cx - ZONE_RADIUS}
              y={cy - 8}
              width={ZONE_RADIUS * 2}
              align="center"
              text={label}
              fontSize={13}
              fontStyle="bold"
              fontFamily="'Inter', 'SF Mono', monospace"
              fill={labelColor}
            />
          </React.Fragment>
        );
      })}
    </Layer>
  );
}
