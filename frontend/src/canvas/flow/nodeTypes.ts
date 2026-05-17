// Central nodeTypes registry for React Flow.
// Import once and pass to <ReactFlow nodeTypes={nodeTypes} />.

import { ActorNode } from "./nodes/ActorNode";
import { ZoneNode } from "./nodes/ZoneNode";
import { PoolLabelNode } from "./nodes/PoolLabelNode";

export const nodeTypes = {
  actorNode: ActorNode,
  zoneNode: ZoneNode,
  poolLabelNode: PoolLabelNode,
} as const;
