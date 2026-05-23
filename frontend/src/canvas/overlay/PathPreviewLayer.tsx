// PathPreviewLayer — Phase 5.1 Stage 4.
//
// Reads agvPaths from the runtime slice (populated by agv_path SocketIO event)
// and renders a dashed polyline for each active AGV path connecting cell centers.
//
// Path is cleared when:
//   - A new agv_path event arrives with a higher epoch (supersedes).
//   - A leg_completed bus_message for that actor's transport slot fires
//     (socketDriver clears the slice entry).
//
// listening={false} — clicks pass through to React Flow nodes.
// NO hardcoded geometry.

import { Layer, Line } from "react-konva";

import type { ViewportTransform } from "./useViewportTransform";
import { useActors, useAgvPaths } from "@/store/selectors";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Grid cell size in canvas pixels (must match backend CELL_PX = 40). */
const CELL_PX = 40;

/** Default color when actor not found in topology. */
const DEFAULT_COLOR = "#888888";

/** Dash pattern [dash, gap] in stage pixels. */
const DASH_PATTERN = [4, 4];

/** Path stroke width in stage pixels. */
const STROKE_WIDTH = 2;

/** Path stroke alpha (30 %). */
const STROKE_ALPHA = 0.3;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function hexToRgba(hex: string, a: number): string {
  const clean = hex.startsWith("#") ? hex.slice(1) : hex;
  const r = parseInt(clean.slice(0, 2), 16) || 136;
  const g = parseInt(clean.slice(2, 4), 16) || 136;
  const b = parseInt(clean.slice(4, 6), 16) || 136;
  return `rgba(${r},${g},${b},${a.toFixed(3)})`;
}

/** Convert [[col, row], ...] to Konva flat points array [x0, y0, x1, y1, ...].
 *  Each cell center is at (col + 0.5) * CELL_PX, (row + 0.5) * CELL_PX. */
function cellsToPoints(cells: [number, number][]): number[] {
  const pts: number[] = [];
  for (const [col, row] of cells) {
    pts.push((col + 0.5) * CELL_PX, (row + 0.5) * CELL_PX);
  }
  return pts;
}

// ---------------------------------------------------------------------------
// PathPreviewLayer
// ---------------------------------------------------------------------------

export interface PathPreviewLayerProps {
  tx: ViewportTransform;
}

export function PathPreviewLayer({ tx }: PathPreviewLayerProps) {
  const agvPaths = useAgvPaths();
  const actors = useActors();

  const entries = Object.entries(agvPaths);

  // Nothing to render when no paths are active.
  if (entries.length === 0) return null;

  return (
    <Layer
      listening={false}
      x={tx.x}
      y={tx.y}
      scaleX={tx.zoom}
      scaleY={tx.zoom}
    >
      {entries.map(([actorId, pathEntry]) => {
        if (pathEntry.cells.length < 2) return null;

        const actor = actors[actorId];
        const color =
          (actor as (typeof actor & { color?: string }) | undefined)?.color ??
          DEFAULT_COLOR;

        const pts = cellsToPoints(pathEntry.cells);

        return (
          <Line
            key={`path-${actorId}-${pathEntry.epoch}`}
            points={pts}
            stroke={hexToRgba(color, STROKE_ALPHA)}
            strokeWidth={STROKE_WIDTH}
            dash={DASH_PATTERN}
            lineCap="round"
            lineJoin="round"
            listening={false}
          />
        );
      })}
    </Layer>
  );
}
