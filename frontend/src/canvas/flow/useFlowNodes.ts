// Derives RF Node arrays from store topology data.
// Actor positions come from topology; zone/pool label nodes are centered via origin [0.5,0.5].

import { useMemo } from "react";
import type { Node } from "@xyflow/react";
import { useActors, useZones, usePool } from "@/store/selectors";
import { nodeStyle } from "./flowConfig";

export function useFlowNodes(): Node[] {
  const actors = useActors();
  const zones = useZones();
  const pool = usePool();

  return useMemo(() => {
    const nodes: Node[] = [];

    // ── Actor nodes ────────────────────────────────────────────────────────
    for (const actor of Object.values(actors)) {
      nodes.push({
        id: actor.id,
        type: "actorNode",
        position: { x: actor.position.x, y: actor.position.y },
        data: { ...actor },
        // Width is set on the card element; RF needs it for hit-test accuracy.
        style: { ...nodeStyle, width: actor.size.w, height: actor.size.h },
        // Prevent RF from adding its own selected class (we manage it from store).
        selected: false,
      });
    }

    // ── Zone chip nodes (centered via origin) ─────────────────────────────
    for (const zone of Object.values(zones)) {
      nodes.push({
        id: zone.id,
        type: "zoneNode",
        position: { x: zone.position.x, y: zone.position.y },
        data: { ...zone },
        style: { ...nodeStyle },
        // origin [0.5, 0.5] so position == chip center (prototype uses translate(-50%,-50%))
        origin: [0.5, 0.5],
        draggable: false,
        selectable: false,
      });
    }

    // ── Pool label node (centered) ────────────────────────────────────────
    // Prototype: left:660px; top:410px with translate(-50%, -50%)
    // → center at (660, 410)
    nodes.push({
      id: "pool-label",
      type: "poolLabelNode",
      position: { x: 660, y: 410 },
      data: { pool },
      style: { ...nodeStyle },
      origin: [0.5, 0.5],
      draggable: false,
      selectable: false,
    });

    return nodes;
  }, [actors, zones, pool]);
}
