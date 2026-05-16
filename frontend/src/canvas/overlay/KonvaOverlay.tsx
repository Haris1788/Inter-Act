// KonvaOverlay - Domain 4 spatial overlay.
//
// Renders arm halos, pool stadium, reachability traces, animated phantom
// trajectory, and dock glows on a react-konva Stage that fills the mounting
// wrapper (absolute inset-0, pointer-events-none via the parent div).
//
// Transform authority: React Flow owns pan/zoom. Every Konva *Layer* receives
// x/y/scaleX/scaleY from useViewportTransform() so all shapes stay locked to
// RF world-space coords without any independent transform.
//
// Konva tree contract: Stage > Layer > (Group | Shape). Layers are the only
// valid Stage children and must NOT be nested - each visual band below is its
// own sibling Layer.
//
// Shape coordinates are authored in 1320×720 stage space (identical to the SVG
// in INTER.act v2.html) so e.g. arm-1 halo is Circle x=285 y=240 r=170.

import { useEffect, useRef, useState } from "react";
import { Stage, Layer, Circle, Rect, Path } from "react-konva";

import { useViewportTransform } from "./useViewportTransform";
import type { ViewportTransform } from "./useViewportTransform";
import {
  useTrajectories,
  useReachability,
  useDockGlows,
} from "@/store/selectors";
import { tokens } from "@/lib/tokens";
import { AgvTokenLayer } from "./AgvTokenLayer";

type Transform = ViewportTransform;

// ---------------------------------------------------------------------------
// Static layer - arm halos, docking rings, pool stadium, source/sink whispers.
// ---------------------------------------------------------------------------

function StaticLayer({ tx }: { tx: Transform }) {
  return (
    <Layer
      listening={false}
      x={tx.x}
      y={tx.y}
      scaleX={tx.zoom}
      scaleY={tx.zoom}
    >
      {/* arm-1 capability halo */}
      <Circle
        x={285}
        y={240}
        radius={170}
        fillRadialGradientStartPoint={{ x: 0, y: 0 }}
        fillRadialGradientEndPoint={{ x: 0, y: 0 }}
        fillRadialGradientStartRadius={0}
        fillRadialGradientEndRadius={170}
        fillRadialGradientColorStops={[
          0,
          "rgba(24,24,27,0.06)",
          0.78,
          "rgba(24,24,27,0)",
        ]}
      />
      <Circle
        x={285}
        y={240}
        radius={170}
        stroke={tokens.line.strong}
        strokeWidth={1}
        dash={[2, 6]}
        opacity={0.8}
      />
      <Circle
        x={285}
        y={240}
        radius={110}
        stroke={tokens.ink[1]}
        strokeWidth={1}
        dash={[3, 4]}
        opacity={0.35}
      />

      {/* arm-2 capability halo */}
      <Circle
        x={1035}
        y={240}
        radius={170}
        fillRadialGradientStartPoint={{ x: 0, y: 0 }}
        fillRadialGradientEndPoint={{ x: 0, y: 0 }}
        fillRadialGradientStartRadius={0}
        fillRadialGradientEndRadius={170}
        fillRadialGradientColorStops={[
          0,
          "rgba(24,24,27,0.06)",
          0.78,
          "rgba(24,24,27,0)",
        ]}
      />
      <Circle
        x={1035}
        y={240}
        radius={170}
        stroke={tokens.line.strong}
        strokeWidth={1}
        dash={[2, 6]}
        opacity={0.8}
      />
      <Circle
        x={1035}
        y={240}
        radius={110}
        stroke={tokens.ink[1]}
        strokeWidth={1}
        dash={[3, 4]}
        opacity={0.35}
      />

      {/* AGV pool stadium */}
      <Rect
        x={350}
        y={410}
        width={620}
        height={200}
        cornerRadius={100}
        fillRadialGradientStartPoint={{ x: 310, y: 100 }}
        fillRadialGradientEndPoint={{ x: 310, y: 100 }}
        fillRadialGradientStartRadius={0}
        fillRadialGradientEndRadius={310}
        fillRadialGradientColorStops={[
          0,
          "rgba(95,143,88,0.08)",
          0.8,
          "rgba(95,143,88,0)",
        ]}
      />
      <Rect
        x={350}
        y={410}
        width={620}
        height={200}
        cornerRadius={100}
        stroke={tokens.poolStroke}
        strokeWidth={1}
        dash={[2, 5]}
        opacity={0.9}
      />

      {/* source / sink whisper strokes */}
      <Path
        data="M 110 240 L 175 240"
        stroke={tokens.line.strong}
        strokeWidth={1}
        dash={[2, 5]}
        opacity={0.9}
      />
      <Path
        data="M 1145 240 L 1210 240"
        stroke={tokens.line.strong}
        strokeWidth={1}
        dash={[2, 5]}
        opacity={0.9}
      />
    </Layer>
  );
}

// ---------------------------------------------------------------------------
// Reachability traces - 6 thin dotted Béziers (its own sibling Layer).
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
// KonvaOverlay - root. Stage holds three sibling Layers, each transformed.
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
        <StaticLayer tx={tx} />
        <ReachabilityLayer tx={tx} traces={reachability} />
        <DynamicLayer
          tx={tx}
          trajectories={trajectories}
          dockGlows={dockGlows}
        />
        <AgvTokenLayer tx={tx} />
      </Stage>
    </div>
  );
}
