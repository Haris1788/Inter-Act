// ReservedCellsLayer — Phase 5.1 Stage 4.
//
// Polls GET /reservations every 1 s and renders each reserved cell as a
// filled Rect at 25% actor-color alpha. The selected actor's reservations
// get a brighter stroke outline (full actor color, 1.5 px).
//
// listening={false} — clicks pass through to React Flow nodes above.
// NO hardcoded geometry: all positions derive from the /reservations snapshot.

import { useEffect, useState } from "react";
import { Layer, Rect } from "react-konva";

import type { ViewportTransform } from "./useViewportTransform";
import { useActors, useSelectedActorId } from "@/store/selectors";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Grid cell size in canvas pixels (must match backend CELL_PX = 40). */
const CELL_PX = 40;

/** Poll interval for /reservations (ms). */
const POLL_MS = 1_000;

/** Default color when actor_id is not found in topology. */
const DEFAULT_COLOR = "#888888";

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

function hexToRgba(hex: string, a: number): string {
  const clean = hex.startsWith("#") ? hex.slice(1) : hex;
  const r = parseInt(clean.slice(0, 2), 16) || 136;
  const g = parseInt(clean.slice(2, 4), 16) || 136;
  const b = parseInt(clean.slice(4, 6), 16) || 136;
  return `rgba(${r},${g},${b},${a.toFixed(3)})`;
}

// ---------------------------------------------------------------------------
// ReservedCellsLayer
// ---------------------------------------------------------------------------

export interface ReservedCellsLayerProps {
  tx: ViewportTransform;
}

export function ReservedCellsLayer({ tx }: ReservedCellsLayerProps) {
  const actors = useActors();
  const selectedActorId = useSelectedActorId();

  const [reservations, setReservations] = useState<ReservationEntry[]>([]);

  useEffect(() => {
    let cancelled = false;

    async function poll() {
      try {
        const r = await fetch("/reservations");
        if (!r.ok || cancelled) return;
        const data = (await r.json()) as ReservationsResponse;
        if (!cancelled) setReservations(data.reservations ?? []);
      } catch {
        // /reservations is best-effort; ignore network errors.
      }
    }

    poll();
    const timer = setInterval(poll, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  // Nothing to render when there are no reservations.
  if (reservations.length === 0) return null;

  return (
    <Layer
      listening={false}
      x={tx.x}
      y={tx.y}
      scaleX={tx.zoom}
      scaleY={tx.zoom}
    >
      {reservations.map((entry) => {
        const actor = actors[entry.actor_id];
        const color =
          (actor as (typeof actor & { color?: string }) | undefined)?.color ??
          DEFAULT_COLOR;
        const isSelected = entry.actor_id === selectedActorId;

        return entry.cells.map(([col, row]) => (
          <Rect
            key={`resv-${entry.actor_id}-${entry.epoch}-${col}-${row}`}
            x={col * CELL_PX}
            y={row * CELL_PX}
            width={CELL_PX}
            height={CELL_PX}
            fill={hexToRgba(color, 0.25)}
            stroke={isSelected ? hexToRgba(color, 1.0) : undefined}
            strokeWidth={isSelected ? 1.5 : 0}
            listening={false}
          />
        ));
      })}
    </Layer>
  );
}
