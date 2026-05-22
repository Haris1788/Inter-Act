// WarehouseGridLayer - Renders the factory floor grid from backend /config.
//
// Drawn in stage-space coordinates (origin, cell_px) from warehouse config.
// Shows: floor panel, grid lines, shared lane, arm stations (colored by arm),
// AGV bays (dashed, color-coded). AGV live positions come from AgvTokenLayer.

import { useEffect, useState } from "react";
import { Layer, Rect, Line, Text } from "react-konva";
import type { ViewportTransform } from "./useViewportTransform";

interface GridConfig {
  cols: number;
  rows: number;
  cell_px: number;
  origin: { x: number; y: number };
  width: number;
  height: number;
}

interface StationConfig {
  cell: [number, number];
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

interface ArmInfo {
  id: number;
  color: string;
  label: string;
}

export function WarehouseGridLayer({ tx }: { tx: ViewportTransform }) {
  const [wh, setWh] = useState<WarehouseConfig | null>(null);
  const [arms, setArms] = useState<ArmInfo[]>([]);

  useEffect(() => {
    fetch("/config")
      .then((r) => r.json())
      .then((cfg) => {
        if (cfg.warehouse?.grid) setWh(cfg.warehouse);
        if (cfg.arms) setArms(cfg.arms);
      })
      .catch(() => {});
  }, []);

  if (!wh) return null;

  const { grid, stations, lane, agvs } = wh;
  const { cols, rows, cell_px: cp, origin } = grid;
  const ox = origin.x;
  const oy = origin.y;
  const W = cols * cp;
  const H = rows * cp;

  const cx = (c: number) => ox + c * cp;
  const cy = (r: number) => oy + r * cp;

  const armColorMap: Record<string, string> = {};
  const armLabelMap: Record<string, string> = {};
  arms.forEach((a) => {
    armColorMap[`arm-${a.id}`] = a.color;
    armLabelMap[`arm-${a.id}`] = a.label;
  });

  const laneCells = lane?.cells || [];

  return (
    <Layer
      listening={false}
      x={tx.x}
      y={tx.y}
      scaleX={tx.zoom}
      scaleY={tx.zoom}
    >
      {/* Floor panel */}
      <Rect x={ox} y={oy} width={W} height={H} fill="#f0f3f5" />

      {/* Grid lines — vertical */}
      {Array.from({ length: cols + 1 }, (_, c) => (
        <Line
          key={`v${c}`}
          points={[cx(c), oy, cx(c), oy + H]}
          stroke="rgba(127,140,141,0.25)"
          strokeWidth={1}
        />
      ))}

      {/* Grid lines — horizontal */}
      {Array.from({ length: rows + 1 }, (_, r) => (
        <Line
          key={`h${r}`}
          points={[ox, cy(r), ox + W, cy(r)]}
          stroke="rgba(127,140,141,0.25)"
          strokeWidth={1}
        />
      ))}

      {/* Shared travel lane (highlighted cells) */}
      {laneCells.map(([c, r], i) => (
        <Rect
          key={`lane${i}`}
          x={cx(c) + 1}
          y={cy(r) + 1}
          width={cp - 2}
          height={cp - 2}
          fill="rgba(52,152,219,0.12)"
        />
      ))}

      {/* Arm stations — colored by their arm */}
      {Object.entries(stations || {}).map(([key, st]) => {
        const [c, r] = st.cell;
        const color =
          (st.served_by && armColorMap[st.served_by]) ||
          (key === "input_arm" ? "#7f8c8d" : "#27ae60");
        const armLabel =
          st.served_by
            ? armLabelMap[st.served_by] || st.served_by
            : key;
        const fontSize = Math.max(10, Math.floor(cp * 0.22));

        return (
          <>
            {/* Station fill */}
            <Rect
              key={`st-fill-${key}`}
              x={cx(c) + 4}
              y={cy(r) + 4}
              width={cp - 8}
              height={cp - 8}
              cornerRadius={6}
              fill={color + "40"}
              stroke={color}
              strokeWidth={2}
            />
            {/* Station label (IN / OUT) inside cell */}
            <Text
              key={`st-lbl-${key}`}
              x={cx(c)}
              y={cy(r) + cp / 2 - fontSize / 2}
              width={cp}
              text={st.label || (key === "input_arm" ? "IN" : "OUT")}
              fontSize={fontSize}
              fontStyle="bold"
              fill={color}
              align="center"
            />
            {/* Arm name above cell */}
            <Text
              key={`st-arm-${key}`}
              x={cx(c)}
              y={cy(r) - 14}
              width={cp}
              text={armLabel}
              fontSize={10}
              fill="#7f8c8d"
              align="center"
            />
          </>
        );
      })}

      {/* AGV bays — dashed outline, "BAY" label inside, AGV name below */}
      {(agvs || []).map((agv) => {
        const [c, r] = agv.bay;
        const bayFontSize = Math.max(9, Math.floor(cp * 0.18));

        return (
          <>
            <Rect
              key={`bay-fill-${agv.id}`}
              x={cx(c) + 4}
              y={cy(r) + 4}
              width={cp - 8}
              height={cp - 8}
              cornerRadius={4}
              fill={agv.color + "1a"}
              stroke={agv.color + "b3"}
              strokeWidth={1.5}
              dash={[4, 3]}
            />
            <Text
              key={`bay-lbl-${agv.id}`}
              x={cx(c)}
              y={cy(r) + cp / 2 - bayFontSize / 2}
              width={cp}
              text="BAY"
              fontSize={bayFontSize}
              fontStyle="bold"
              fill={agv.color + "80"}
              align="center"
            />
            <Text
              key={`bay-name-${agv.id}`}
              x={cx(c)}
              y={cy(r) + cp + 2}
              width={cp}
              text={agv.label}
              fontSize={10}
              fill="#7f8c8d"
              align="center"
            />
          </>
        );
      })}
    </Layer>
  );
}
