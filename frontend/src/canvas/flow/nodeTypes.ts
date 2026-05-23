// Central nodeTypes registry for React Flow.
// Phase 5.1 Stage 1: ZoneNode removed (deleted). ActorNode is now invisible
// (transparent shell for RF drag/pan/zoom math; Konva is the visual renderer).
// Phase 5.1 bug-fix: PoolLabelNode deleted — no dashboard widgets on canvas.

import { ActorNode } from "./nodes/ActorNode";

export const nodeTypes = {
  actorNode: ActorNode,
} as const;
