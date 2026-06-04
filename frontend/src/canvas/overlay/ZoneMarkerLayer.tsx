// ZoneMarkerLayer — Phase 7 Stage C: Konva markers for zone actors (kind="zone").
// Ported from E:/Inter Act (canvas-as-source-of-truth). Store-driven only — NO
// /config, NO warehouse.stations, NO static geometry.
//
// Renders one labeled dashed circle per zone actor:
//   · source (subtype="source"): green-ish circle + "IN"  label.
//   · sink   (subtype="sink"):   red-ish   circle + "OUT" label.
//
// Position = actor.position (top-left of the 100×100 box) + half-size, so the
// circle is centred in the actor box. The RF node is an invisible shell that
// owns drag/select; this layer is listening={false} and follows the actor's
// live position, so dragging the node moves the marker in real time.

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
    <Layer listening={false} x={tx.x} y={tx.y} scaleX={tx.zoom} scaleY={tx.zoom}>
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
