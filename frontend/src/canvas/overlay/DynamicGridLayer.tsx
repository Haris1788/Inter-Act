// DynamicGridLayer — Phase 5.1 Stage 4.
//
// Renders tiny dots ONLY at currently active grid cells (radius 2 px,
// rgba(150, 150, 150, 0.3)). Active set is the union of:
//   1. Occupied cells — cells where actorRuntime[id].cell is set.
//      (AGV cell from agv_update/agv_position-derived runtimePosition.)
//   2. Reserved cells — from the same 1 Hz /reservations poll (shared
//      component-level state to avoid a duplicate network request; here
//      we accept a second poll as the layer is independent by contract).
//   3. Current path cells — from agvPaths[*].cells in the runtime slice.
//
// Empty world → renders nothing ("empty world = blank canvas" invariant).
// listening={false} — clicks pass through.

import { useEffect, useState } from "react";
import { Layer, Circle } from "react-konva";

import type { ViewportTransform } from "./useViewportTransform";
import { useActorRuntimeAll, useAgvPaths } from "@/store/selectors";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Grid cell size in canvas pixels (must match backend CELL_PX = 40). */
const CELL_PX = 40;

/** Dot radius in canvas pixels. */
const DOT_RADIUS = 2;

/** Dot fill colour. */
const DOT_COLOR = "rgba(150,150,150,0.3)";

/** Poll interval for /reservations (ms). */
const POLL_MS = 1_000;

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface ReservationEntry {
  task_id: string;
  actor_id: string;
  epoch: number;
  cells: [number, number][];
}

interface ReservationsResponse {
  reservations: ReservationEntry[];
  ts: number;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Cell key string for de-duplication. */
function cellKey(col: number, row: number): string {
  return `${col}:${row}`;
}

// ---------------------------------------------------------------------------
// DynamicGridLayer
// ---------------------------------------------------------------------------

export interface DynamicGridLayerProps {
  tx: ViewportTransform;
}

export function DynamicGridLayer({ tx }: DynamicGridLayerProps) {
  const actorRuntime = useActorRuntimeAll();
  const agvPaths = useAgvPaths();

  // Local /reservations poll (independent of ReservedCellsLayer).
  const [reservedCells, setReservedCells] = useState<[number, number][]>([]);

  useEffect(() => {
    let cancelled = false;

    async function poll() {
      try {
        const r = await fetch("/reservations");
        if (!r.ok || cancelled) return;
        const data = (await r.json()) as ReservationsResponse;
        if (cancelled) return;
        const cells: [number, number][] = [];
        for (const entry of data.reservations ?? []) {
          for (const cell of entry.cells) {
            cells.push(cell);
          }
        }
        setReservedCells(cells);
      } catch {
        // best-effort
      }
    }

    poll();
    const timer = setInterval(poll, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  // ── Derive active cell set ────────────────────────────────────────────────

  const activeCells = new Map<string, [number, number]>();

  // 1. Occupancy cells from actorRuntime (cell field on AGV runtime records).
  for (const rt of Object.values(actorRuntime)) {
    // The runtime slice stores cell as part of agv_update; it lands in the
    // runtimePosition or can be inferred from pixel position. For simplicity
    // we derive the snapped cell from runtimePosition when available.
    const rtp = (rt as typeof rt & { runtimePosition?: { x: number; y: number } }).runtimePosition;
    if (rtp != null) {
      const col = Math.floor(rtp.x / CELL_PX);
      const row = Math.floor(rtp.y / CELL_PX);
      activeCells.set(cellKey(col, row), [col, row]);
    }
    // Also read the explicit cell field if the runtime carries it.
    const cellField = (rt as typeof rt & { cell?: [number, number] | null }).cell;
    if (Array.isArray(cellField) && cellField.length >= 2) {
      const col = cellField[0] as number;
      const row = cellField[1] as number;
      activeCells.set(cellKey(col, row), [col, row]);
    }
  }

  // 2. Reserved cells from /reservations poll.
  for (const [col, row] of reservedCells) {
    activeCells.set(cellKey(col, row), [col, row]);
  }

  // 3. Current path cells from agvPaths slice.
  for (const pathEntry of Object.values(agvPaths)) {
    for (const [col, row] of pathEntry.cells) {
      activeCells.set(cellKey(col, row), [col, row]);
    }
  }

  // Empty world → render nothing.
  if (activeCells.size === 0) return null;

  const dots = Array.from(activeCells.values());

  return (
    <Layer
      listening={false}
      x={tx.x}
      y={tx.y}
      scaleX={tx.zoom}
      scaleY={tx.zoom}
    >
      {dots.map(([col, row]) => (
        <Circle
          key={cellKey(col, row)}
          x={(col + 0.5) * CELL_PX}
          y={(row + 0.5) * CELL_PX}
          radius={DOT_RADIUS}
          fill={DOT_COLOR}
          listening={false}
        />
      ))}
    </Layer>
  );
}
