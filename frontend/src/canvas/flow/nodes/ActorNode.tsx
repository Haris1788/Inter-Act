// ActorNode - Phase 5.1 Stage 1: invisible RF positional shell.
//
// React Flow still tracks this node for pan/zoom/drag math. It renders
// a zero-opacity, pointer-events-none div so RF events (drag, selection,
// context-menu) continue to fire correctly. ALL visual rendering is now
// done by Konva layers (ArmRenderer / AgvTokenLayer / AgvTrailLayer).

import { memo } from "react";
import type { NodeProps } from "@xyflow/react";
import type { TopologyActor } from "@/store/types";

/** The `data` payload stored on each RF ActorNode. */
export type ActorNodeData = TopologyActor;

export const ActorNode = memo(function ActorNode(_props: NodeProps) {
  // Transparent 1×1 shell. RF uses the node's `width`/`height` fields
  // (set in useFlowNodes) for position math; this element carries no
  // visual. pointerEvents none so hover/click falls through to the pane.
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
