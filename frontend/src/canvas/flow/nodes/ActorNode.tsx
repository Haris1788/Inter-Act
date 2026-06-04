// ActorNode — Phase 7 Stage B: invisible RF positional shell (E parity).
//
// React Flow still tracks this node for pan/zoom/drag/selection math, but it
// renders a zero-opacity, pointer-events-none 1×1 div. ALL visual rendering is
// done by Konva layers (ArmRenderer / AgvTokenLayer / ZoneMarkerLayer). The RF
// node wrapper is sized via node.width/height (set in useFlowNodes), so the
// drag/click hit area still covers the actor's box.
//
// Divergence/transient: the old card carried inline label-edit + bid/coherence
// pills + an on-canvas selection ring. Label-edit now lives in the Inspector
// (store-backed via updateActor). Selection feedback is Inspector-only until a
// later stage adds Konva selection styling.

import { memo } from "react";
import type { NodeProps } from "@xyflow/react";
import type { TopologyActor } from "@/store/types";

/** The `data` payload stored on each RF ActorNode. */
export type ActorNodeData = TopologyActor;

export const ActorNode = memo(function ActorNode(_props: NodeProps) {
  return (
    <div
      style={{
        width: 1,
        height: 1,
        opacity: 0,
        pointerEvents: "none",
      }}
    />
  );
});
