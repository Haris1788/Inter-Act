// Derives RF Node arrays from store topology data.
// Phase 7 Stage B: every actor (arm/agv/zone) is ONE invisible `actorNode`
// shell (E parity). RF uses the node for drag/pan/zoom/selection math; all
// visuals are drawn by Konva layers. The old zone branch, the konvaOnly
// card-vs-shell logic, the /config seededArmIds fetch, and the pool label are
// all gone — the canvas is store-driven only.

import { useMemo } from "react";
import type { Node } from "@xyflow/react";
import { useActors } from "@/store/selectors";
import { nodeStyle } from "./flowConfig";

export function useFlowNodes(): Node[] {
  const actors = useActors();

  return useMemo(() => {
    const nodes: Node[] = [];

    // ── Actor nodes (invisible RF shells; arm/agv/zone alike) ──────────────
    for (const actor of Object.values(actors)) {
      nodes.push({
        id: actor.id,
        type: "actorNode",
        position: { x: actor.position.x, y: actor.position.y },
        data: { ...actor },
        // First-class dimensions: RF's wrapper uses node.width/height for the
        // hit area + drag math + MiniMap. Konva reads positions from the store.
        width: actor.size.w,
        height: actor.size.h,
        style: { ...nodeStyle },
        // Selection is managed from the store, not RF's own class.
        selected: false,
      });
    }

    return nodes;
  }, [actors]);
}
