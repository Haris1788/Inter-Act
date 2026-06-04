// WarehouseGridLayer — rewritten Sneh-port "open canvas" version.
//
// Old version drew a full floor panel + grid lines on every cell, which
// professor / the team called "too gridded". This version draws ONLY the
// semantically meaningful markers:
//   · IN / OUT zone circles at the input + output stations (soft dashed)
//   · BAY squares at each AGV's starting cell (dashed in AGV colour)
//   · The shared lane as a subtle translucent corridor band
//
// Everything else (cells, AGV motion, routes, packages, arms) is drawn by
// other layers on top of an otherwise blank canvas.

import { useEffect, useState } from "react";
import { Layer, Rect, Circle, Text, Line } from "react-konva";
import type { ViewportTransform } from "./useViewportTransform";

interface GridConfig {
  cols: number;
  rows: number;
  cell_px: number;
  origin: { x: number; y: number };
}

interface StationConfig {
  cell: [number, number];
  dock?: [number, number];
  label: string;
  served_by?: string;
}

interface AgvConfig {
  id: string;
  label: string;
  color: string;
  bay: [number, number];
}

interface WarehouseConfig {
  grid: GridConfig;
  stations: Record<string, StationConfig>;
  lane: { cells: [number, number][] };
  agvs: AgvConfig[];
}

export function WarehouseGridLayer({ tx }: { tx: ViewportTransform }) {
  const [wh, setWh] = useState<WarehouseConfig | null>(null);

  useEffect(() => {
    fetch("/config")
      .then((r) => r.json())
      .then((cfg) => {
        if (cfg.warehouse?.grid) setWh(cfg.warehouse);
      })
      .catch(() => {});
  }, []);

  if (!wh) return null;

  const { grid, stations, lane, agvs } = wh;
  const { cell_px: cp, origin } = grid;
  const ox = origin.x;
  const oy = origin.y;

  const cellX = (c: number) => ox + c * cp;
  const cellY = (r: number) => oy + r * cp;

  const laneCells = lane?.cells || [];

  // Lane as a single corridor polyline (visually softer than per-cell fills).
  const lanePts: number[] = [];
  for (const [c, r] of laneCells) {
    lanePts.push(cellX(c) + cp / 2, cellY(r) + cp / 2);
  }

  return (
    <Layer
      listening={false}
      x={tx.x}
      y={tx.y}
      scaleX={tx.zoom}
      scaleY={tx.zoom}
    >
      {/* Shared lane corridor — translucent band. */}
      {lanePts.length >= 4 && (
        <Line
          points={lanePts}
          stroke="rgba(120,150,180,0.18)"
          strokeWidth={cp * 0.85}
          lineCap="round"
          lineJoin="round"
        />
      )}

      {/* Arm stations: IN / OUT as soft dashed circles with bold label. */}
      {Object.entries(stations || {}).map(([key, st]) => {
        const [c, r] = st.cell;
        const isInput = key === "input_arm";
        const label = st.label || (isInput ? "IN" : "OUT");
        const fillColor = isInput
          ? "rgba(80,180,100,0.10)"
          : "rgba(220,90,90,0.10)";
        const strokeColor = isInput
          ? "rgba(60,160,80,0.70)"
          : "rgba(190,60,60,0.70)";
        const labelColor = isInput
          ? "rgba(40,130,60,0.95)"
          : "rgba(160,40,40,0.95)";
        const radius = cp * 0.46;
        const ccx = cellX(c) + cp / 2;
        const ccy = cellY(r) + cp / 2;
        return (
          <>
            <Circle
              key={`zone-${key}`}
              x={ccx}
              y={ccy}
              radius={radius}
              fill={fillColor}
              stroke={strokeColor}
              strokeWidth={1.5}
              dash={[5, 4]}
            />
            <Text
              key={`zone-lbl-${key}`}
              x={ccx - radius}
              y={ccy - cp * 0.12}
              width={radius * 2}
              align="center"
              text={label}
              fontSize={Math.max(11, cp * 0.22)}
              fontStyle="bold"
              fontFamily="Inter, monospace"
              fill={labelColor}
            />
          </>
        );
      })}

      {/* AGV bays: filled grey square + dashed AGV-coloured outline + label. */}
      {(agvs || []).map((agv) => {
        const [c, r] = agv.bay;
        const ccx = cellX(c) + cp / 2;
        const ccy = cellY(r) + cp / 2;
        const size = cp * 0.72;
        return (
          <>
            <Rect
              key={`bay-fill-${agv.id}`}
              x={ccx - size / 2}
              y={ccy - size / 2}
              width={size}
              height={size}
              cornerRadius={4}
              fill="rgba(120,120,120,0.18)"
              stroke={agv.color}
              strokeWidth={1.5}
              dash={[4, 3]}
              opacity={0.95}
            />
            <Text
              key={`bay-lbl-${agv.id}`}
              x={ccx - cp * 0.5}
              y={ccy - 6}
              width={cp}
              align="center"
              text="BAY"
              fontSize={Math.max(9, cp * 0.16)}
              fontStyle="bold"
              fill="rgba(70,70,70,0.85)"
            />
            <Text
              key={`bay-name-${agv.id}`}
              x={ccx - cp * 0.5}
              y={ccy + size / 2 + 2}
              width={cp}
              align="center"
              text={agv.label}
              fontSize={10}
              fill={agv.color}
              fontStyle="bold"
            />
          </>
        );
      })}
    </Layer>
  );
}
