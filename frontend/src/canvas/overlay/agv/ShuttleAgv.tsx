// ShuttleAgv — low-profile body (50×30 px).
// This is the existing rounded-rect vehicle promoted to its own component.
// No visual change from the original AgvTokenLayer body render.

import { Group, Rect, RegularPolygon, Line, Circle } from "react-konva";
import type { AgvSilhouetteProps } from "./types";

const BODY_W = 50;
const BODY_H = 30;
const POINTER_SIZE = 10;
const DOT_RADIUS = 4;

const STATUS_DOT_COLORS: Record<string, string> = {
  idle:      "#16a085",
  moving:    "#2563eb",
  loading:   "#eab308",
  unloading: "#eab308",
  returning: "#3b82f6",
  faulted:   "#dc2626",
  stopped:   "#6b7280",
  ok:        "#16a085",
  warn:      "#eab308",
  busy:      "#2563eb",
  down:      "#dc2626",
};
const DEFAULT_DOT_COLOR = "#9ca3af";

function dotColor(status: string): string {
  return STATUS_DOT_COLORS[status] ?? DEFAULT_DOT_COLOR;
}

function darkenHex(hex: string, amount = 0.25): string {
  const r = Math.round(parseInt(hex.slice(1, 3), 16) * (1 - amount));
  const g = Math.round(parseInt(hex.slice(3, 5), 16) * (1 - amount));
  const b = Math.round(parseInt(hex.slice(5, 7), 16) * (1 - amount));
  return `rgb(${r},${g},${b})`;
}

export function ShuttleAgv({
  theta,
  color,
  isSelected,
  carried,
  status,
}: AgvSilhouetteProps) {
  const headingDeg = (theta * 180) / Math.PI;
  const strokeColor = isSelected ? color : darkenHex(color, 0.30);
  const strokeWidth = isSelected ? 2.5 : 1.5;

  return (
    <>
      {/* Rotated vehicle group */}
      <Group rotation={headingDeg}>
        {/* Drop shadow */}
        <Rect
          x={-BODY_W / 2 + 2}
          y={-BODY_H / 2 + 2}
          width={BODY_W}
          height={BODY_H}
          cornerRadius={6}
          fill="rgba(0,0,0,0.18)"
        />

        {/* Main chassis body */}
        <Rect
          x={-BODY_W / 2}
          y={-BODY_H / 2}
          width={BODY_W}
          height={BODY_H}
          cornerRadius={6}
          fill={color}
          stroke={strokeColor}
          strokeWidth={strokeWidth}
        />

        {/* Front heading pointer */}
        <RegularPolygon
          x={BODY_W / 2 + POINTER_SIZE * 0.55}
          y={0}
          sides={3}
          radius={POINTER_SIZE * 0.7}
          rotation={90}
          fill={strokeColor}
          opacity={0.9}
        />

        {/* Cargo indicator */}
        {carried && (
          <>
            <Rect
              x={-BODY_W * 0.28}
              y={-BODY_H * 0.35}
              width={BODY_W * 0.56}
              height={BODY_H * 0.7}
              cornerRadius={3}
              fill="#d9a566"
              stroke="#7a4a1e"
              strokeWidth={1}
            />
            <Line
              points={[0, -BODY_H * 0.35, 0, BODY_H * 0.35]}
              stroke="#7a4a1e"
              strokeWidth={0.8}
              opacity={0.7}
            />
          </>
        )}
      </Group>

      {/* Status dot — world-space (not rotated) */}
      <Circle
        x={BODY_W / 2 - DOT_RADIUS}
        y={BODY_H / 2 + 5 + DOT_RADIUS}
        radius={DOT_RADIUS}
        fill={dotColor(status)}
        stroke="rgba(0,0,0,0.35)"
        strokeWidth={0.8}
      />
    </>
  );
}
