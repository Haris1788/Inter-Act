// Central nodeTypes registry for React Flow.
// Phase 5.1 Stage 1: ZoneNode removed (deleted). ActorNode is now invisible
// (transparent shell for RF drag/pan/zoom math; Konva is the visual renderer).

import { ActorNode } from "./nodes/ActorNode";
import { PoolLabelNode } from "./nodes/PoolLabelNode";

export const nodeTypes = {
  actorNode: ActorNode,
  poolLabelNode: PoolLabelNode,
} as const;
