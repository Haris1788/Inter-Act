// topologyBounds - Phase 5.1 Stage 1.
//
// Derives a bounding rectangle from a set of TopologyActors.
// Used by CanvasViewport to set the initial viewport / fitView source
// WITHOUT reading /config warehouse dimensions.

import type { TopologyActor } from "@/store/types";

/**
 * Compute the union bounding box of all actors (each expanded by its
 * reach_px radius), then pad by `pad` pixels on every side.
 *
 * Empty topology → sensible default centered at origin (800×600).
 */
export function topologyBounds(
  actors: TopologyActor[],
  pad = 160,
): { x: number; y: number; width: number; height: number } {
  if (actors.length === 0) {
    return { x: 0, y: 0, width: 800, height: 600 };
  }

  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;

  for (const a of actors) {
    const r = a.reach_px ?? 40;
    minX = Math.min(minX, a.position.x - r);
    minY = Math.min(minY, a.position.y - r);
    maxX = Math.max(maxX, a.position.x + r);
    maxY = Math.max(maxY, a.position.y + r);
  }

  return {
    x: minX - pad,
    y: minY - pad,
    width: maxX - minX + 2 * pad,
    height: maxY - minY + 2 * pad,
  };
}
