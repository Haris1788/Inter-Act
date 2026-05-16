// CanvasViewport — Domain 3 (React Flow topology layer + canvas chrome).
//
// Layer stack inside the container:
//   z-[1]  ReactFlow (pan/zoom/nodes) — single transform authority
//   z-[2]  KonvaOverlay (Domain 4 — halos, glows, trajectories)
//   z-[3]  LabelsOverlay (Domain 4 — cap-tags, dock-state labels)
//   z-[4+] Canvas chrome (toolbar, meta pill, zoom cluster, fleet card)
//
// Domain 4 files don't exist yet (parallel build); TS errors on those imports
// are expected and ignored per spec.

import { useCallback, useRef } from "react";
import {
  ReactFlow,
  ReactFlowProvider,
  type NodeChange,
  type Node,
} from "@xyflow/react";

import { useStore } from "@/store";
import { useFlowNodes } from "./flow/useFlowNodes";
import { useFitViewport } from "./flow/useFitViewport";
import { nodeTypes } from "./flow/nodeTypes";
import { flowDefaults } from "./flow/flowConfig";
import { CanvasToolbar } from "./chrome/CanvasToolbar";
import { CanvasMeta } from "./chrome/CanvasMeta";
import { ZoomCluster } from "./chrome/ZoomCluster";
import { FleetCard } from "./chrome/FleetCard";
import { GoalBar } from "@/chrome/GoalBar";
import { BusStrip } from "@/chrome/BusStrip";

// Domain 4 — stubs exist now; real implementations from Domain 4 will replace them.
import { KonvaOverlay } from "@/canvas/overlay/KonvaOverlay";
import { LabelsOverlay } from "@/canvas/labels/LabelsOverlay";

/** Inner component — must be inside ReactFlowProvider to access RF hooks. */
function FlowInner() {
  const containerRef = useRef<HTMLDivElement>(null);
  const setSelectedActor = useStore((s) => s.setSelectedActor);
  const moveActor = useStore((s) => s.moveActor);

  const nodes = useFlowNodes();
  const { fitToStage } = useFitViewport(containerRef);

  // Forward node drag → store.
  const handleNodesChange = useCallback(
    (changes: NodeChange<Node>[]) => {
      for (const change of changes) {
        if (change.type === "position" && change.position) {
          // Only commit when dragging ends or on position changes.
          moveActor(change.id, { x: change.position.x, y: change.position.y });
        }
      }
    },
    [moveActor],
  );

  // Click on a node → select actor.
  const handleNodeClick = useCallback(
    (_: React.MouseEvent, node: Node) => {
      // Zone/pool-label nodes are not selectable actors.
      if (node.type === "actorNode") {
        setSelectedActor(node.id);
      }
    },
    [setSelectedActor],
  );

  // Click on empty pane → deselect.
  const handlePaneClick = useCallback(() => {
    setSelectedActor(null);
  }, [setSelectedActor]);

  return (
    // The ref goes on this wrapper so ResizeObserver tracks the actual pane size.
    <div ref={containerRef} className="relative h-full w-full overflow-hidden">
      {/* z-[1] — React Flow (owns pan/zoom; the only transform authority) */}
      <ReactFlow
        nodes={nodes}
        edges={[]}
        nodeTypes={nodeTypes}
        onNodesChange={handleNodesChange}
        onNodeClick={handleNodeClick}
        onPaneClick={handlePaneClick}
        {...flowDefaults}
        // Disable all RF node-selection visuals; we manage selection in store.
        selectNodesOnDrag={false}
        multiSelectionKeyCode={null}
        // No handles, no edge connections.
        connectOnClick={false}
        // Prevent "selected" class from RF on nodes (we control it via store).
        nodesFocusable={false}
        // Disable default RF fit-view on init; useFitViewport handles it.
        fitView={false}
        className="h-full w-full"
        style={{ background: "transparent" }}
      />

      {/* z-[2] — Konva overlay (Domain 4) */}
      <div className="pointer-events-none absolute inset-0 z-[2]">
        <KonvaOverlay />
      </div>

      {/* z-[3] — Labels overlay (Domain 4) */}
      <div className="pointer-events-none absolute inset-0 z-[3]">
        <LabelsOverlay />
      </div>

      {/* z-[4+] — Canvas chrome (toolbar, meta, zoom, fleet, goal, bus) */}
      <CanvasToolbar onFit={fitToStage} />
      <CanvasMeta />
      <ZoomClusterWrapper onFit={fitToStage} />
      <FleetCard />
      {/* goal-bar (z-6) + bus-strip (z-5) — self-positioned canvas overlays */}
      <GoalBar />
      <BusStrip />
    </div>
  );
}

/** ZoomCluster must be inside ReactFlowProvider to call useReactFlow/useViewport. */
function ZoomClusterWrapper({ onFit }: { onFit: () => void }) {
  return <ZoomCluster onFit={onFit} />;
}

/**
 * CanvasViewport — exported named component consumed by App shell.
 * Wraps everything in ReactFlowProvider so all child hooks work.
 */
export function CanvasViewport() {
  return (
    <ReactFlowProvider>
      {/* dot-grid::before provides the background dot pattern via CSS */}
      <section className="dot-grid relative h-full overflow-hidden border-b border-line bg-bg-canvas">
        <FlowInner />
      </section>
    </ReactFlowProvider>
  );
}
