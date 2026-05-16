// useViewportTransform - reads RF transform from the store synchronously
// (same frame as RF, pre-paint). Must be called inside the ReactFlowProvider tree.
//
// We intentionally use useStore (raw RF store) NOT useViewport() - the latter
// is one tick late and causes drift between Konva shapes and RF nodes.

import { useStore } from "@xyflow/react";

export interface ViewportTransform {
  x: number;
  y: number;
  zoom: number;
}

export function useViewportTransform(): ViewportTransform {
  // Default before RF initializes its transform on first paint.
  const transform = useStore((s) => s.transform) ?? [0, 0, 1];
  return { x: transform[0], y: transform[1], zoom: transform[2] };
}
