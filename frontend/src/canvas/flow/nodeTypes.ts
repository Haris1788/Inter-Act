// Central nodeTypes registry for React Flow.
// ActorNode is the only RF node — an invisible positional shell; Konva renders
// every visual (arms, AGVs, zones). Phase 7 retired the per-kind RF nodes.

import { ActorNode } from "./nodes/ActorNode";

export const nodeTypes = {
  actorNode: ActorNode,
} as const;
