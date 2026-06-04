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
import { Stage, Layer, Circle, Path, Line, Group, Rect, Text } from "react-konva";

import { useViewportTransform } from "./useViewportTransform";
import type { ViewportTransform } from "./useViewportTransform";
import {
  useTrajectories,
  useReachability,
  useDockGlows,
  useArmAngles,
  useTablePkgs,
  useActors,
} from "@/store/selectors";
import { PayloadLayer } from "./PayloadLayer";
import { useStore } from "@/store";
import { tokens } from "@/lib/tokens";
import { AgvTokenLayer } from "./AgvTokenLayer";
import { ZoneMarkerLayer } from "./ZoneMarkerLayer";

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
  armKey: string;
}

function StaticLayer({ tx }: { tx: Transform }) {
  // Phase 7 Stage D: fully store-driven. Reach halos for EVERY kind:"arm"
  // actor, positioned from actor.position. No /config fetch, no seeded/authored
  // split, no warehouse.stations — the canvas is the source of truth. Reach
  // scale uses a fixed CELL_PX constant (was warehouse.grid.cell_px).
  const actors = useActors();
  const visibleHalos: ArmHalo[] = Object.values(actors)
    .filter((a) => a.kind === "arm")
    .map((a) => ({
      x: a.position.x + a.size.w / 2,
      y: a.position.y + a.size.h / 2,
      color: authoredArmColor(a.id),
      outerR: ARM_CELL_PX * 1.6,
      innerR: ARM_CELL_PX * 1.0,
      armKey: a.id,
    }));

  return (
    <Layer
      listening={false}
      x={tx.x}
      y={tx.y}
      scaleX={tx.zoom}
      scaleY={tx.zoom}
    >
      {visibleHalos.flatMap((h, i) => [
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

// Authoring-time parity: authored arms (in topology.actors but not declared in
// the backend /config warehouse stations) have no station geometry. Give them a
// palette colour so they read like the seeded arms instead of falling back to
// the "#888" no-colour grey.
const AUTHORED_ARM_COLORS = ["#e74c3c", "#27ae60", "#2980b9", "#e67e22", "#8e44ad"];
function authoredArmColor(id: string): string {
  const n = parseInt(id.replace(/\D/g, ""), 10) || 1;
  return AUTHORED_ARM_COLORS[(n - 1) % AUTHORED_ARM_COLORS.length];
}

// Phase 7 Stage D: fixed reach/cell scale for store-driven arm rendering.
// Replaces warehouse.grid.cell_px (was the only reason these layers read
// /config). 120 == the warehouse cell size the authored path already used.
const ARM_CELL_PX = 120;

function ArmLayer({ tx }: { tx: Transform }) {
  // Phase 7 Stage D: fully store-driven. One assembly per kind:"arm" actor at
  // actor.position. No /config, no warehouse.stations, no seeded/authored split.
  // Dock defaults east (cx + ARM_CELL_PX); table is its mirror. Reach scale is
  // the fixed ARM_CELL_PX constant. Angle still comes from the armAngles store
  // (live arm_angle_update events) — that is runtime state, not /config.
  const armAngles = useArmAngles();
  const tablePkgs = useTablePkgs();
  const actors = useActors();

  const stations: ArmStation[] = Object.values(actors)
    .filter((a) => a.kind === "arm")
    .map((a) => {
      const cx = a.position.x + a.size.w / 2;
      const cy = a.position.y + a.size.h / 2;
      return {
        name: a.id,
        x: cx,
        y: cy,
        dockX: cx + ARM_CELL_PX,
        dockY: cy,
        color: authoredArmColor(a.id),
        cellPx: ARM_CELL_PX,
        armKey: a.id,
      };
    });

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

        // Sneh-port: package render moved to PayloadLayer. We keep tablePkgs
        // available (it's still used for log/status), but render no per-table
        // packages here — that would duplicate what PayloadLayer draws.
        const pkgs: { id: string; color: string }[] = [];
        void tablePkgs;
        const PKG = cp * 0.12;
        const PKG_GAP = cp * 0.05;

        return (
          <Group key={stn.name}>
            {/* Table surface — soft, minimal. The actual package render
                lives in PayloadLayer so the table is just a hint. */}
            <Rect
              x={tableCX - zoneW / 2}
              y={tableCY - zoneH / 2}
              width={zoneW}
              height={zoneH}
              fill="rgba(0,0,0,0.04)"
              stroke={stn.color}
              strokeWidth={1}
              opacity={0.7}
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
            {/* Sneh-port: gripper-carry package render moved to PayloadLayer.
                That layer resolves arm-carrier payloads to (tipX,tipY) using
                the same station + angle math, so the package follows the
                gripper exactly — no duplicate render with the on-AGV crate. */}
            {/* US 34.1 (Sneh-port c228768 + 119cd01): actor-id label below
                the assembly. Placed below the outer reach halo so it's
                always visible regardless of which way the stick is pointing,
                and black with weight 600 for legibility on the light canvas. */}
            <Text
              x={stn.x - cp * 0.8}
              y={stn.y + reach + 14}
              width={cp * 1.6}
              align="center"
              text={stn.armKey || stn.name}
              fontSize={11}
              fill="rgba(0,0,0,0.85)"
              fontStyle="600"
            />
          </Group>
        );
      })}
    </Layer>
  );
}

// ---------------------------------------------------------------------------
// AgvRouteLayer (US 34.1) — polyline of each AGV's currently announced route.
// Driven by `agvRoutes` in the runtime slice, populated by the socketDriver
// from `intent` bus events whose payload carries `path_cells`. The cell
// sequence is the OUTPUT of the AGV's A* / smart-path planner (US 34 Phase
// 6), so what the operator sees here is exactly the route the AGV will
// walk — not a heuristic guess by the UI.
// ---------------------------------------------------------------------------

interface GridGeo {
  cellPx: number;
  ox: number;
  oy: number;
}

function AgvRouteLayer({ tx }: { tx: Transform }) {
  const agvRoutes = useStore((s) => s.agvRoutes);
  const [geo, setGeo] = useState<GridGeo | null>(null);
  const [dashOffset, setDashOffset] = useState(0);
  const rafRef = useRef<number | null>(null);
  const lastTsRef = useRef<number | null>(null);
  const offsetRef = useRef(0);

  useEffect(() => {
    fetch("/config")
      .then((r) => r.json())
      .then((cfg) => {
        const wh = cfg.warehouse;
        if (!wh?.grid?.cell_px) return;
        // BUG WAS HERE: origin is inside wh.grid (not wh.origin which is {}).
        // That made ox/oy undefined → polyline coords became NaN → route
        // never rendered. THIS is why "I can't see any colored route".
        const origin = wh.grid.origin ?? wh.origin ?? { x: 0, y: 0 };
        setGeo({
          cellPx: wh.grid.cell_px,
          ox: origin.x ?? 0,
          oy: origin.y ?? 0,
        });
      })
      .catch(() => {});
  }, []);

  // Marching ants — same cadence as DynamicLayer phantoms so the eye reads
  // "route preview" not "static dashed line".
  useEffect(() => {
    function tick(ts: number) {
      const dt = lastTsRef.current === null ? 0 : ts - lastTsRef.current;
      lastTsRef.current = ts;
      offsetRef.current = (offsetRef.current - (dt / 1000) * 14) % -32;
      setDashOffset(offsetRef.current);
      rafRef.current = requestAnimationFrame(tick);
    }
    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    };
  }, []);

  if (!geo) return null;
  const cp = geo.cellPx;
  const cx = (c: number) => geo.ox + c * cp + cp / 2;
  const cy = (r: number) => geo.oy + r * cp + cp / 2;

  const routes = Object.values(agvRoutes);

  return (
    <Layer
      listening={false}
      x={tx.x}
      y={tx.y}
      scaleX={tx.zoom}
      scaleY={tx.zoom}
    >
      {routes.flatMap((route) => {
        if (!route.cells || route.cells.length < 2) return [];
        const pts: number[] = [];
        for (const [c, r] of route.cells) {
          pts.push(cx(c), cy(r));
        }
        const lastCell = route.cells[route.cells.length - 1];
        return [
          // Highlighted cells along the chosen route — semi-transparent
          // squares so the operator can READ which cells the AGV will visit.
          ...route.cells.map(([c, r], i) => (
            <Rect
              key={`route-cell-${route.agvId}-${i}`}
              x={cx(c) - cp / 2}
              y={cy(r) - cp / 2}
              width={cp}
              height={cp}
              fill={route.color}
              opacity={0.10}
              stroke={route.color}
              strokeWidth={1}
              cornerRadius={2}
            />
          )),
          // Solid underlay — strong corridor band.
          <Line
            key={`route-fill-${route.agvId}`}
            points={pts}
            stroke={route.color}
            strokeWidth={cp * 0.30}
            lineCap="round"
            lineJoin="round"
            opacity={0.35}
          />,
          // Marching-ants top stroke — animated. Thick + opaque so it pops.
          <Line
            key={`route-ants-${route.agvId}`}
            points={pts}
            stroke={route.color}
            strokeWidth={cp * 0.12}
            dash={[12, 8]}
            dashOffset={dashOffset}
            lineCap="round"
            lineJoin="round"
            opacity={1}
            shadowColor="rgba(0,0,0,0.4)"
            shadowBlur={3}
          />,
          // Dot at each waypoint cell.
          ...route.cells.map(([c, r], i) => (
            <Circle
              key={`route-dot-${route.agvId}-${i}`}
              x={cx(c)}
              y={cy(r)}
              radius={cp * 0.07}
              fill={route.color}
              stroke="white"
              strokeWidth={1.2}
              opacity={0.9}
            />
          )),
          // DESTINATION marker — bold ring at the AGV's final cell so the
          // operator knows where it's headed even before it arrives.
          <Circle
            key={`route-dest-${route.agvId}`}
            x={cx(lastCell[0])}
            y={cy(lastCell[1])}
            radius={cp * 0.36}
            stroke={route.color}
            strokeWidth={cp * 0.06}
            opacity={0.85}
            shadowColor={route.color}
            shadowBlur={cp * 0.25}
            shadowOpacity={0.6}
          />,
        ];
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
        <StaticLayer tx={tx} />
        <ReachabilityLayer tx={tx} traces={reachability} />
        <DynamicLayer
          tx={tx}
          trajectories={trajectories}
          dockGlows={dockGlows}
        />
        {/* US 34.1: live A* route polyline per AGV, below the arms so the
            arm bodies still draw over a route that crosses a station. */}
        <AgvRouteLayer tx={tx} />
        <ArmLayer tx={tx} />
        <AgvTokenLayer tx={tx} />
        {/* Phase 7 Stage C: store-driven IN/OUT zone markers (kind:"zone"
            actors). Above arms/AGVs so the dashed circle + label read clearly;
            follows the actor position live as the RF shell is dragged. */}
        <ZoneMarkerLayer tx={tx} />
        {/* Sneh-port unified package render: ONE entity per package whose
            position is driven by its carrier (zone/arm/AGV). Sits above the
            arm + AGV layers so packages are always visible on top of them. */}
        <PayloadLayer tx={tx} />
      </Stage>
    </div>
  );
}
