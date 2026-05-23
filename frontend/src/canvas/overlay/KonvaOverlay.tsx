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
import { Stage, Layer, Circle, Path, Line, Group, Rect } from "react-konva";

import { useViewportTransform } from "./useViewportTransform";
import type { ViewportTransform } from "./useViewportTransform";
import {
  useTrajectories,
  useReachability,
  useDockGlows,
  useArmAngles,
  useTablePkgs,
} from "@/store/selectors";
import { tokens } from "@/lib/tokens";
import { AgvTokenLayer } from "./AgvTokenLayer";
import { AgvTrailLayer } from "./AgvTrailLayer";
import { ArmRenderer } from "./ArmRenderer";
import { WarehouseGridLayer } from "./WarehouseGridLayer";

type Transform = ViewportTransform;

// ---------------------------------------------------------------------------
// Static layer — arm reach halos, driven by /config station positions.
// Replaces hardcoded coordinates so halos always align with the warehouse grid.
// ---------------------------------------------------------------------------

interface ArmHalo {
  x: number;
  y: number;
  color: string;
  outerR: number;
  innerR: number;
}

function StaticLayer({ tx }: { tx: Transform }) {
  const [halos, setHalos] = useState<ArmHalo[]>([]);

  useEffect(() => {
    fetch("/config")
      .then((r) => r.json())
      .then((cfg) => {
        const wh = cfg.warehouse;
        if (!wh?.stations || !wh?.grid) return;
        const cp: number = wh.grid.cell_px;
        const outerR = cp * 1.6;
        const innerR = cp * 1.0;

        const colorMap: Record<string, string> = {};
        (cfg.arms ?? []).forEach((a: { id: number; color?: string }) => {
          colorMap[`arm-${a.id}`] = a.color ?? "#888";
        });

        setHalos(
          Object.values(wh.stations).map((st: unknown) => {
            const s = st as {
              px: { x: number; y: number };
              served_by?: string;
            };
            return {
              x: s.px.x,
              y: s.px.y,
              color: (s.served_by ? colorMap[s.served_by] : null) ?? "#888",
              outerR,
              innerR,
            };
          }),
        );
      })
      .catch(() => {});
  }, []);

  return (
    <Layer
      listening={false}
      x={tx.x}
      y={tx.y}
      scaleX={tx.zoom}
      scaleY={tx.zoom}
    >
      {halos.flatMap((h, i) => [
        // Radial glow fill
        <Circle
          key={`halo-glow-${i}`}
          x={h.x}
          y={h.y}
          radius={h.outerR}
          fillRadialGradientStartPoint={{ x: 0, y: 0 }}
          fillRadialGradientEndPoint={{ x: 0, y: 0 }}
          fillRadialGradientStartRadius={0}
          fillRadialGradientEndRadius={h.outerR}
          fillRadialGradientColorStops={[
            0,
            "rgba(24,24,27,0.05)",
            0.78,
            "rgba(24,24,27,0)",
          ]}
        />,
        // Outer dashed ring (arm color)
        <Circle
          key={`halo-outer-${i}`}
          x={h.x}
          y={h.y}
          radius={h.outerR}
          stroke={h.color}
          strokeWidth={1.5}
          dash={[3, 8]}
          opacity={0.55}
        />,
        // Inner dashed ring (arm color, subtle)
        <Circle
          key={`halo-inner-${i}`}
          x={h.x}
          y={h.y}
          radius={h.innerR}
          stroke={h.color}
          strokeWidth={1}
          dash={[3, 5]}
          opacity={0.25}
        />,
      ])}
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
// Arm layer - rotating arm bodies, tables, packages, dock zones.
// ---------------------------------------------------------------------------

interface ArmStation {
  name: string;
  x: number;
  y: number;
  dockX: number;
  dockY: number;
  color: string;
  cellPx: number;
  armKey: string;
}

function ArmLayer({ tx }: { tx: Transform }) {
  const [stations, setStations] = useState<ArmStation[]>([]);
  const armAngles = useArmAngles();
  const tablePkgs = useTablePkgs();

  useEffect(() => {
    fetch("/config")
      .then((r) => r.json())
      .then((cfg) => {
        const wh = cfg.warehouse;
        if (!wh?.stations || !wh?.grid) return;
        const cp: number = wh.grid.cell_px;
        const colorMap: Record<string, string> = {};
        (cfg.arms ?? []).forEach((a: { id: number; color?: string }) => {
          colorMap[`arm-${a.id}`] = a.color ?? "#888";
        });
        const result: ArmStation[] = [];
        for (const [name, raw] of Object.entries(wh.stations)) {
          const s = raw as {
            px: { x: number; y: number };
            dock_px?: { x: number; y: number };
            served_by?: string;
          };
          if (!s.px) continue;
          const armKey = s.served_by ?? "";
          const dock = s.dock_px ?? s.px;
          result.push({
            name,
            x: s.px.x,
            y: s.px.y,
            dockX: dock.x,
            dockY: dock.y,
            color: colorMap[armKey] ?? "#888",
            cellPx: cp,
            armKey,
          });
        }
        setStations(result);
      })
      .catch(() => {});
  }, []);

  return (
    <Layer
      listening={false}
      x={tx.x}
      y={tx.y}
      scaleX={tx.zoom}
      scaleY={tx.zoom}
    >
      {stations.map((stn) => {
        const cp = stn.cellPx;
        // Vector from arm pivot to the AGV dock (where the AGV actually parks).
        const dx = stn.dockX - stn.x;
        const dy = stn.dockY - stn.y;
        // Reach = far enough that the gripper touches the AGV at the dock and,
        // swept to the opposite side, reaches the table. The table is the
        // mirror of the dock across the pivot.
        const reach = Math.hypot(dx, dy) || cp;
        const tableCX = stn.x - dx;
        const tableCY = stn.y - dy;

        const angle = armAngles[stn.armKey] ?? 270;
        const rad = (angle * Math.PI) / 180;
        const armLen = reach;
        const elbowX = stn.x + Math.cos(rad) * armLen * 0.5;
        const elbowY = stn.y + Math.sin(rad) * armLen * 0.5;
        const tipX = stn.x + Math.cos(rad) * armLen;
        const tipY = stn.y + Math.sin(rad) * armLen;

        const zoneW = cp * 0.78;
        const zoneH = cp * 0.42;

        const pkgs = (tablePkgs[stn.name] ?? []) as {
          id: string;
          color: string;
        }[];
        const PKG = cp * 0.12;
        const PKG_GAP = cp * 0.05;

        return (
          <Group key={stn.name}>
            {/* Table surface (packages stage here) */}
            <Rect
              x={tableCX - zoneW / 2}
              y={tableCY - zoneH / 2}
              width={zoneW}
              height={zoneH}
              fill="rgba(55,60,85,0.6)"
              stroke={stn.color}
              strokeWidth={1.5}
              cornerRadius={4}
            />
            {/* Packages waiting on the table */}
            {pkgs.slice(0, 5).map((pkg, pi) => (
              <Rect
                key={pkg.id}
                x={tableCX - zoneW / 2 + PKG_GAP + pi * (PKG + PKG_GAP)}
                y={tableCY - PKG / 2}
                width={PKG}
                height={PKG}
                fill={pkg.color}
                stroke="rgba(0,0,0,0.25)"
                strokeWidth={0.6}
                cornerRadius={2}
              />
            ))}
            {/* Dock zone — drawn AT the real AGV parking cell */}
            <Rect
              x={stn.dockX - zoneW / 2}
              y={stn.dockY - zoneH / 2}
              width={zoneW}
              height={zoneH}
              fill="rgba(255,255,255,0.03)"
              stroke={stn.color}
              strokeWidth={1.2}
              dash={[6, 4]}
              cornerRadius={4}
              opacity={0.55}
            />
            {/* Arm: upper segment + forearm (two-link, reads as a robot arm) */}
            <Line
              points={[stn.x, stn.y, elbowX, elbowY, tipX, tipY]}
              stroke={stn.color}
              strokeWidth={cp * 0.05}
              lineCap="round"
              lineJoin="round"
            />
            {/* Elbow joint */}
            <Circle
              x={elbowX}
              y={elbowY}
              radius={cp * 0.05}
              fill={stn.color}
            />
            {/* Shoulder pivot base */}
            <Circle
              x={stn.x}
              y={stn.y}
              radius={cp * 0.11}
              fill={stn.color}
            />
            <Circle
              x={stn.x}
              y={stn.y}
              radius={cp * 0.05}
              fill="rgba(255,255,255,0.55)"
            />
            {/* Gripper claw at the arm tip */}
            <Circle
              x={tipX}
              y={tipY}
              radius={cp * 0.075}
              fill={stn.color}
              stroke="rgba(0,0,0,0.3)"
              strokeWidth={1}
            />
          </Group>
        );
      })}
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
        <WarehouseGridLayer tx={tx} />
        <StaticLayer tx={tx} />
        <ReachabilityLayer tx={tx} traces={reachability} />
        <DynamicLayer
          tx={tx}
          trajectories={trajectories}
          dockGlows={dockGlows}
        />
        <ArmLayer tx={tx} />
        {/* Stage 6 Track A — topology-driven arm articulation.
            ArmRenderer reads arm actors from the topology slice (kind="arm")
            and drives their pivot/stick/gripper from arm_angle_update events.
            Mounted as a sibling Layer to AgvTokenLayer for render isolation. */}
        <ArmRenderer tx={tx} />
        {/* Stage 8 Track B — AGV path trail overlay.
            Mounted BEFORE AgvTokenLayer so trails draw behind vehicle tokens.
            selectedTaskId is undefined here; Track C will pass it when the
            replay cursor is active (via a prop drilled from ReplayPanel). */}
        <AgvTrailLayer tx={tx} />
        <AgvTokenLayer tx={tx} />
      </Stage>
    </div>
  );
}
