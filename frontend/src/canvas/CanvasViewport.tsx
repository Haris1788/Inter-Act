// CanvasViewport - React Flow topology authoring layer + canvas chrome.
//
// Layer stack:
//   z-[1]  ReactFlow (pan/zoom/nodes) - single transform authority
//   z-[2]  KonvaOverlay (runtime spatial - halos, glows, trajectories)
//   z-[3]  LabelsOverlay (cap-tags, dock-state labels)
//   z-[4+] Canvas chrome (toolbar, meta, zoom, fleet, goal, bus, context menu)
//
// Phase 4: this is now an EDITOR. Drag/drop creates actors, toolbar modes
// switch RF interaction, keyboard shortcuts delete/duplicate. Konva stays a
// pure runtime slave to the RF transform (coexistence preserved).

import { useCallback, useEffect, useRef } from "react";
import {
  ReactFlow,
  ReactFlowProvider,
  MiniMap,
  useReactFlow,
  type NodeChange,
  type Node,
} from "@xyflow/react";

import { useStore } from "@/store";
import {
  useEditorMode,
  useFocusRequest,
  useSelectedActorId,
} from "@/store/selectors";
import { createActorFromPalette } from "@/runtime/nodeFactory";
import { DRAG_MIME } from "@/lib/dragContract";
import { useFlowNodes } from "./flow/useFlowNodes";
import { useFitViewport } from "./flow/useFitViewport";
import { nodeTypes } from "./flow/nodeTypes";
import { flowDefaults } from "./flow/flowConfig";
import { CanvasToolbar } from "./chrome/CanvasToolbar";
import { CanvasMeta } from "./chrome/CanvasMeta";
import { ZoomCluster } from "./chrome/ZoomCluster";
import { ContextMenu } from "./chrome/ContextMenu";
import { GoalBar } from "@/chrome/GoalBar";
import { KonvaOverlay } from "@/canvas/overlay/KonvaOverlay";
import { LabelsOverlay } from "@/canvas/labels/LabelsOverlay";

function FlowInner() {
  const containerRef = useRef<HTMLDivElement>(null);
  const rf = useReactFlow();
  const mode = useEditorMode();
  const focusRequest = useFocusRequest();
  const selectedId = useSelectedActorId();

  const setSelectedActor = useStore((s) => s.setSelectedActor);
  const moveActor = useStore((s) => s.moveActor);
  const addActor = useStore((s) => s.addActor);
  const removeActor = useStore((s) => s.removeActor);
  const markDirty = useStore((s) => s.markDirty);
  const toggleSelection = useStore((s) => s.toggleSelection);
  const clearSelection = useStore((s) => s.clearSelection);
  const closeContextMenu = useStore((s) => s.closeContextMenu);
  const openContextMenu = useStore((s) => s.openContextMenu);

  const nodes = useFlowNodes();
  const { fitToStage } = useFitViewport(containerRef);

  // Focus-on-selection: center + gently zoom the requested actor. Smooth,
  // restrained - operational navigation at scale, not a camera fly.
  useEffect(() => {
    if (!focusRequest) return;
    void rf.fitView({
      nodes: [{ id: focusRequest.id }],
      duration: 450,
      minZoom: 0.6,
      maxZoom: 1.2,
      padding: 0.55,
    });
  }, [focusRequest, rf]);

  const handleNodesChange = useCallback(
    (changes: NodeChange<Node>[]) => {
      for (const c of changes) {
        if (c.type === "position" && c.position) {
          moveActor(c.id, { x: c.position.x, y: c.position.y });
          markDirty();
        }
      }
    },
    [moveActor, markDirty],
  );

  const handleNodeClick = useCallback(
    (e: React.MouseEvent, node: Node) => {
      if (node.type !== "actorNode") return;
      const additive = e.shiftKey || e.metaKey || e.ctrlKey;
      toggleSelection(node.id, additive);
      setSelectedActor(node.id);
      closeContextMenu();
    },
    [toggleSelection, setSelectedActor, closeContextMenu],
  );

  const handleNodeContextMenu = useCallback(
    (e: React.MouseEvent, node: Node) => {
      if (node.type !== "actorNode") return;
      e.preventDefault();
      setSelectedActor(node.id);
      toggleSelection(node.id, false);
      openContextMenu({ x: e.clientX, y: e.clientY, nodeId: node.id });
    },
    [setSelectedActor, toggleSelection, openContextMenu],
  );

  const handlePaneClick = useCallback(() => {
    setSelectedActor(null);
    clearSelection();
    closeContextMenu();
  }, [setSelectedActor, clearSelection, closeContextMenu]);

  // ── drag/drop authoring ────────────────────────────────────────────────
  const onDragOver = useCallback((e: React.DragEvent) => {
    if (e.dataTransfer.types.includes(DRAG_MIME)) {
      e.preventDefault();
      e.dataTransfer.dropEffect = "copy";
    }
  }, []);

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      const key = e.dataTransfer.getData(DRAG_MIME);
      if (!key) return;
      e.preventDefault();
      const center = rf.screenToFlowPosition({ x: e.clientX, y: e.clientY });

      // Phase 7: ONE path for every kind (arm/agv/zone) — all are TopologyActors
      // created via createActorFromPalette and added with addActor.
      const actor = createActorFromPalette(
        key,
        center,
        useStore.getState().actors,
      );
      if (!actor) return;
      addActor(actor);
      markDirty();
      setSelectedActor(actor.id);
      toggleSelection(actor.id, false);
    },
    [rf, addActor, markDirty, setSelectedActor, toggleSelection],
  );

  // ── keyboard shortcuts ─────────────────────────────────────────────────
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (
        t &&
        (t.tagName === "INPUT" ||
          t.tagName === "TEXTAREA" ||
          t.isContentEditable)
      )
        return;
      const s = useStore.getState();
      const meta = e.metaKey || e.ctrlKey;
      if (meta && e.key.toLowerCase() === "d") {
        e.preventDefault();
        const ids = s.selectedIds.length
          ? s.selectedIds
          : s.selectedActorId
            ? [s.selectedActorId]
            : [];
        const dups = ids
          .map((id) => s.duplicateActor(id))
          .filter(Boolean) as string[];
        if (dups.length) {
          s.setSelection(dups);
          s.markDirty();
        }
        return;
      }
      if (e.key === "Delete" || e.key === "Backspace") {
        const ids = s.selectedIds.length
          ? s.selectedIds
          : s.selectedActorId
            ? [s.selectedActorId]
            : [];
        if (!ids.length) return;
        e.preventDefault();
        ids.forEach((id) => removeActor(id));
        s.clearSelection();
        s.markDirty();
        return;
      }
      if (meta && e.key.toLowerCase() === "a") {
        e.preventDefault();
        s.setSelection(Object.keys(s.actors));
        return;
      }
      // Arrow-nudge selected actors (precision placement). Shift = 10px.
      if (e.key.startsWith("Arrow")) {
        const ids = s.selectedIds.length
          ? s.selectedIds
          : s.selectedActorId
            ? [s.selectedActorId]
            : [];
        if (!ids.length) return;
        e.preventDefault();
        const d = e.shiftKey ? 10 : 1;
        const dx = e.key === "ArrowLeft" ? -d : e.key === "ArrowRight" ? d : 0;
        const dy = e.key === "ArrowUp" ? -d : e.key === "ArrowDown" ? d : 0;
        for (const id of ids) {
          const a = s.actors[id];
          if (a)
            s.moveActor(id, { x: a.position.x + dx, y: a.position.y + dy });
        }
        s.markDirty();
        return;
      }
      if (!meta && e.key === "f") {
        const id = s.selectedActorId ?? s.selectedIds[0];
        if (id) {
          e.preventDefault();
          s.requestFocus(id);
        }
        return;
      }
      if (!meta && e.key === "v") s.setMode("select");
      if (!meta && e.key === "h") s.setMode("pan");
      if (!meta && e.key === "e") s.setMode("edit");
      if (e.key === "Escape") {
        s.clearSelection();
        s.setSelectedActor(null);
        s.closeContextMenu();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // handler reads via useStore.getState() - stable, no deps needed
  }, []);

  // Interaction mode → RF behavior. select: drag nodes; pan: hand-drag canvas;
  // edit: drag nodes + box-select.
  const panOnDrag = mode === "pan";
  const nodesDraggable = mode !== "pan";
  const selectionOnDrag = mode === "edit";

  return (
    <div
      ref={containerRef}
      className="relative h-full w-full overflow-hidden"
      onDragOver={onDragOver}
      onDrop={onDrop}
    >
      <ReactFlow
        nodes={nodes}
        edges={[]}
        nodeTypes={nodeTypes}
        onNodesChange={handleNodesChange}
        onNodeClick={handleNodeClick}
        onNodeContextMenu={handleNodeContextMenu}
        onPaneClick={handlePaneClick}
        {...flowDefaults}
        panOnDrag={panOnDrag}
        nodesDraggable={nodesDraggable}
        selectionOnDrag={selectionOnDrag}
        selectNodesOnDrag={false}
        multiSelectionKeyCode={null}
        connectOnClick={false}
        nodesFocusable={false}
        fitView={false}
        className="h-full w-full"
        style={{ background: "transparent" }}
      >
        {/* Real, functional overview: live nodes, viewport mask synced to
            pan/zoom, click/drag to navigate. Styled as the prototype
            "TOPOLOGY · OVERVIEW" card; the label strip is the chrome
            element just below (covers the reserved top band). */}
        <MiniMap
          position="bottom-right"
          pannable
          zoomable
          ariaLabel="Topology overview"
          className="!rounded-md !border !border-line !bg-bg-surface !shadow-e2"
          style={{ width: 180, height: 120, margin: 12 }}
          maskColor="rgba(244,243,239,0.62)"
          maskStrokeColor="rgba(201,198,189,0.75)"
          nodeBorderRadius={3}
          nodeStrokeWidth={1}
          nodeColor={(n) =>
            n.id === selectedId
              ? "rgba(24,24,27,0.30)"
              : n.type === "actorNode"
                ? (n.data as { kind?: string })?.kind === "agv"
                  ? "rgba(122,132,110,0.22)"
                  : "rgba(96,94,88,0.24)"
                : "rgba(201,198,189,0.16)"
          }
          nodeStrokeColor={(n) =>
            n.id === selectedId ? "#18181B" : "rgba(96,94,88,0.55)"
          }
        />
      </ReactFlow>

      {/* Prototype card label - overlays the reserved 22px top band of the
          MiniMap (which is offset via paddingTop) so it reads as a framed
          "TOPOLOGY · OVERVIEW" card without blocking the live thumbnail. */}
      <div
        className="pointer-events-none absolute z-[5] flex items-center rounded-t-md border border-b-0 border-line bg-bg-surface px-2 text-[10px] uppercase tracking-[0.08em] text-ink-4"
        style={{ right: 12, bottom: 110, width: 180, height: 22 }}
      >
        Topology · overview
      </div>

      <div className="pointer-events-none absolute inset-0 z-[2]">
        <KonvaOverlay />
      </div>
      <div className="pointer-events-none absolute inset-0 z-[3]">
        <LabelsOverlay />
      </div>

      <CanvasToolbar onFit={fitToStage} />
      <CanvasMeta />
      <ZoomCluster onFit={fitToStage} />
      <GoalBar />
      <ContextMenu />
    </div>
  );
}

export function CanvasViewport() {
  return (
    <ReactFlowProvider>
      <section className="dot-grid relative h-full overflow-hidden border-b border-line bg-bg-canvas">
        <FlowInner />
      </section>
    </ReactFlowProvider>
  );
}
