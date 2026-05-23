// KonvaOverlay - Phase 5.1 Stage 1: Konva-as-sole-renderer.
//
// Layer stack (bottom to top):
//   ReachabilityLayer  — dotted Bézier reachability traces
//   DynamicLayer       — phantom trajectories (marching ants) + dock glows
//   ArmRenderer        — Phase 5 articulated arm (pivot / stick / gripper)
//   AgvTrailLayer      — Phase 5 polyline trails
//   AgvTokenLayer      — Phase 5 vehicle rounded-rect bodies
//
// Deleted in Stage 1:
//   WarehouseGridLayer — static grid patch from /config (GONE)
//   StaticLayer        — arm reach halos from /config stations (GONE)
//   ArmLayer           — legacy arm box / icon rendering from /config (GONE)
//
// Transform authority: React Flow owns pan/zoom. Every Konva Layer receives
// x/y/scaleX/scaleY from useViewportTransform() so all shapes stay locked
// to RF world-space coords without any independent transform.

import { useEffect, useRef, useState } from "react";
import { Stage, Layer, Circle, Path } from "react-konva";

import { useViewportTransform } from "./useViewportTransform";
import type { ViewportTransform } from "./useViewportTransform";
import {
  useTrajectories,
  useReachability,
  useDockGlows,
} from "@/store/selectors";
import { tokens } from "@/lib/tokens";
import { AgvTokenLayer } from "./AgvTokenLayer";
import { AgvTrailLayer } from "./AgvTrailLayer";
import { ArmRenderer } from "./ArmRenderer";
import { DynamicGridLayer } from "./DynamicGridLayer";
import { ReservedCellsLayer } from "./ReservedCellsLayer";
import { PathPreviewLayer } from "./PathPreviewLayer";
import { NegotiationHaloLayer } from "./NegotiationHaloLayer";

type Transform = ViewportTransform;

// ---------------------------------------------------------------------------
// Reachability traces - dotted Béziers (sibling Layer).
// ---------------------------------------------------------------------------

function ReachabilityLayer({
  tx,
  traces,
}: {
  tx: Transform;
  traces: { id: string; path: string; opacity: number }[];
}) {
  return (
    <Layer
      listening={false}
      x={tx.x}
      y={tx.y}
      scaleX={tx.zoom}
      scaleY={tx.zoom}
    >
      {traces.map((t) => (
        <Path
          key={t.id}
          data={t.path}
          stroke={tokens.trace}
          strokeWidth={1}
          dash={[1, 5]}
          lineCap="round"
          opacity={t.opacity}
        />
      ))}
    </Layer>
  );
}

// ---------------------------------------------------------------------------
// Dynamic layer - phantom trajectories (marching ants) + dock glows.
// ---------------------------------------------------------------------------

function DynamicLayer({
  tx,
  trajectories,
  dockGlows,
}: {
  tx: Transform;
  trajectories: {
    id: string;
    path: string;
    phantom: boolean;
    opacity: number;
  }[];
  dockGlows: { id: string; at: { x: number; y: number }; intensity: number }[];
}) {
  const [dashOffset, setDashOffset] = useState(0);
  const rafRef = useRef<number | null>(null);
  const lastTsRef = useRef<number | null>(null);
  const offsetRef = useRef(0);

  useEffect(() => {
    function tick(ts: number) {
      const dt = lastTsRef.current === null ? 0 : ts - lastTsRef.current;
      lastTsRef.current = ts;
      // 11 units/s → full 22-unit cycle == 2 s (matches the SVG prototype).
      offsetRef.current = (offsetRef.current - (dt / 1000) * 11) % -22;
      setDashOffset(offsetRef.current);
      rafRef.current = requestAnimationFrame(tick);
    }
    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    };
  }, []);

  const phantoms = trajectories.filter((t) => t.phantom);
  const planned = trajectories.filter((t) => !t.phantom);

  return (
    <Layer
      listening={false}
      x={tx.x}
      y={tx.y}
      scaleX={tx.zoom}
      scaleY={tx.zoom}
    >
      {dockGlows.map((g) => (
        <Circle
          key={g.id}
          x={g.at.x}
          y={g.at.y}
          radius={28}
          fillRadialGradientStartPoint={{ x: 0, y: 0 }}
          fillRadialGradientEndPoint={{ x: 0, y: 0 }}
          fillRadialGradientStartRadius={0}
          fillRadialGradientEndRadius={28}
          fillRadialGradientColorStops={[
            0,
            "rgba(24,24,27,0.10)",
            0.8,
            "rgba(24,24,27,0)",
          ]}
          opacity={g.intensity * 0.7}
        />
      ))}

      {planned.map((t) => (
        <Path
          key={t.id}
          data={t.path}
          stroke={tokens.ink[1]}
          strokeWidth={1.4}
          dash={[2, 7]}
          lineCap="round"
          opacity={t.opacity}
        />
      ))}

      {phantoms.map((t) => (
        <Path
          key={t.id}
          data={t.path}
          stroke={tokens.ink[1]}
          strokeWidth={1.6}
          dash={[5, 6]}
          dashOffset={dashOffset}
          lineCap="round"
          opacity={t.opacity}
        />
      ))}
    </Layer>
  );
}

// ---------------------------------------------------------------------------
// KonvaOverlay - root. Stage holds sibling Layers, each RF-transform-locked.
// ---------------------------------------------------------------------------

export function KonvaOverlay() {
  const tx = useViewportTransform();
  const trajectories = useTrajectories();
  const reachability = useReachability();
  const dockGlows = useDockGlows();

  const containerRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 1320, h: 720 });

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect;
      if (rect) setSize({ w: rect.width, h: rect.height });
    });
    ro.observe(el);
    setSize({ w: el.clientWidth || 1320, h: el.clientHeight || 720 });
    return () => ro.disconnect();
  }, []);

  return (
    <div ref={containerRef} style={{ width: "100%", height: "100%" }}>
      <Stage width={size.w} height={size.h} listening={false}>
        <ReachabilityLayer tx={tx} traces={reachability} />
        <DynamicLayer
          tx={tx}
          trajectories={trajectories}
          dockGlows={dockGlows}
        />
        {/* Phase 5.1 Stage 4 — dynamic spatial overlays (lowest: background dots) */}
        <DynamicGridLayer tx={tx} />
        {/* Phase 5.1 Stage 4 — reserved cell fills (mid: actor-color rects) */}
        <ReservedCellsLayer tx={tx} />
        {/* Phase 5.1 Stage 4 — planned path dashed lines (above reservations) */}
        <PathPreviewLayer tx={tx} />
        {/* Phase 5.1 Stage 5 Track A — negotiation halos (behind actor bodies) */}
        <NegotiationHaloLayer tx={tx} />
        {/* Phase 5 arm articulation (pivot / stick / gripper) */}
        <ArmRenderer tx={tx} />
        {/* Phase 5 AGV trail polylines — drawn behind vehicle bodies */}
        <AgvTrailLayer tx={tx} />
        {/* Phase 5 AGV vehicle tokens — topmost Konva layer */}
        <AgvTokenLayer tx={tx} />
      </Stage>
    </div>
  );
}
