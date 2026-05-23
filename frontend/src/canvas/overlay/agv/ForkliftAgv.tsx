// ForkliftAgv — taller upright body (50×40 px) with two parallel forks
// extending from the front edge along the heading direction.
// Forks: 10×3 each, gap 6 px between them, slightly darker than body.

import { Group, Rect, RegularPolygon, Line, Circle } from "react-konva";
import type { AgvSilhouetteProps } from "./types";

const BODY_W = 50;
const BODY_H = 40;
const POINTER_SIZE = 10;
const DOT_RADIUS = 4;

// Fork geometry (local space; forks extend along +x from the front face)
const FORK_L = 14;   // length (along heading)
const FORK_T = 3;    // thickness (across heading)
const FORK_GAP = 6;  // centre-to-centre gap between the two forks
const FORK_OFFSET = FORK_GAP / 2 + FORK_T / 2; // from centre to fork mid-y

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

export function ForkliftAgv({
  theta,
  color,
  isSelected,
  carried,
  status,
}: AgvSilhouetteProps) {
  const headingDeg = (theta * 180) / Math.PI;
  const strokeColor = isSelected ? color : darkenHex(color, 0.30);
  const strokeWidth = isSelected ? 2.5 : 1.5;
  const forkColor = darkenHex(color, 0.40);

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
          cornerRadius={5}
          fill="rgba(0,0,0,0.18)"
        />

        {/* Main chassis body — taller than shuttle */}
        <Rect
          x={-BODY_W / 2}
          y={-BODY_H / 2}
          width={BODY_W}
          height={BODY_H}
          cornerRadius={5}
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

        {/* Forks — two parallel prongs from the front face (+x edge) */}
        {/* Upper fork */}
        <Rect
          x={BODY_W / 2}
          y={-FORK_OFFSET - FORK_T / 2}
          width={FORK_L}
          height={FORK_T}
          cornerRadius={1}
          fill={forkColor}
        />
        {/* Lower fork */}
        <Rect
          x={BODY_W / 2}
          y={FORK_OFFSET - FORK_T / 2}
          width={FORK_L}
          height={FORK_T}
          cornerRadius={1}
          fill={forkColor}
        />

        {/* Cargo indicator — sits on body face */}
        {carried && (
          <>
            <Rect
              x={-BODY_W * 0.28}
              y={-BODY_H * 0.28}
              width={BODY_W * 0.56}
              height={BODY_H * 0.56}
              cornerRadius={3}
              fill="#d9a566"
              stroke="#7a4a1e"
              strokeWidth={1}
            />
            <Line
              points={[0, -BODY_H * 0.28, 0, BODY_H * 0.28]}
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
