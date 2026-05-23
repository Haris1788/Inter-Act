// Derives RF Node arrays from store topology data.
// Phase 5.1 Stage 1: ZoneNode removed. Actor nodes are invisible shells
// (RF uses them for drag/pan/zoom math; Konva layers do all visual rendering).

import { useMemo } from "react";
import type { Node } from "@xyflow/react";
import { useActors, usePool } from "@/store/selectors";
import { nodeStyle } from "./flowConfig";

export function useFlowNodes(): Node[] {
  const actors = useActors();
  const pool = usePool();

  return useMemo(() => {
    const nodes: Node[] = [];

    // ── Actor nodes (invisible RF shells for position/drag math) ───────────
    for (const actor of Object.values(actors)) {
      nodes.push({
        id: actor.id,
        type: "actorNode",
        position: { x: actor.position.x, y: actor.position.y },
        data: { ...actor },
        // First-class dimensions: RF uses node.width/height for drag math
        // and viewport bounds. Konva reads positions from topologySlice.
        width: actor.size.w,
        height: actor.size.h,
        style: { ...nodeStyle },
        // Prevent RF from adding its own selected class (managed from store).
        selected: false,
      });
    }

    // ── Pool label node (centered) ─────────────────────────────────────────
    // Prototype: left:660px; top:410px with translate(-50%, -50%)
    // → center at (660, 410)
    nodes.push({
      id: "pool-label",
      type: "poolLabelNode",
      position: { x: 660, y: 410 },
      data: { pool },
      width: 240,
      height: 64,
      style: { ...nodeStyle },
      origin: [0.5, 0.5],
      draggable: false,
      selectable: false,
    });

    return nodes;
  }, [actors, pool]);
}
