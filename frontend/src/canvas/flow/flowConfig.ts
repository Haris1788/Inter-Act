// Shared React Flow configuration constants.
// Import and spread onto <ReactFlow /> props.

import type { DefaultEdgeOptions } from "@xyflow/react";
import { STAGE_W, STAGE_H } from "@/lib/stageCoords";

const EXTENT_PAD = 1500;

export const flowDefaults = {
  // No edges this phase.
  nodesConnectable: false as const,
  // Allow dragging actors to update position.
  nodesDraggable: true as const,
  // Disable built-in selection box (we handle it via clicks).
  selectionOnDrag: false as const,
  // Disable RF default node styling (background, border, padding).
  nodeOrigin: [0, 0] as [number, number],
  // Viewport constraints.
  minZoom: 0.4,
  maxZoom: 2,
  translateExtent: [
    [-EXTENT_PAD, -EXTENT_PAD],
    [STAGE_W + EXTENT_PAD, STAGE_H + EXTENT_PAD],
  ] as [[number, number], [number, number]],
  // We provide our own dot-grid; no RF background.
  // Edges: empty for now.
  defaultEdgeOptions: {} as DefaultEdgeOptions,
  // Suppress attribution.
  proOptions: { hideAttribution: true },
  // ── Phase 6 ergonomics + scalability ──
  // No snap-to-grid: precision-first. Freeform drag + 1px arrow-nudge stay
  // consistent (a coarse snap fought fine placement and re-snapped nudged
  // actors on the next drag).
  // A click shouldn't start a drag — improves selection/inline-edit precision.
  nodeDragThreshold: 4,
  // Cull off-viewport nodes — keeps large topologies smooth.
  onlyRenderVisibleElements: true as const,
} as const;

/** Default node style: wipe out all RF injected chrome. */
export const nodeStyle: React.CSSProperties = {
  padding: 0,
  border: "none",
  borderRadius: 0,
  background: "transparent",
};
