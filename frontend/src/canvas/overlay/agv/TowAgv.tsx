// TowAgv — compact body (45×28 px) with a short towing hitch
// (~5×4 px rect) on the BACK edge (−x direction in local space).

import { Group, Rect, RegularPolygon, Line } from "react-konva";
import type { AgvSilhouetteProps } from "./types";
import { StatusDot } from "./StatusDot";

const BODY_W = 45;
const BODY_H = 28;
const POINTER_SIZE = 9;
const DOT_RADIUS = 4;

// Hitch geometry — on the rear face (−x edge)
const HITCH_W = 5;
const HITCH_H = 4;

function darkenHex(hex: string, amount = 0.25): string {
  const r = Math.round(parseInt(hex.slice(1, 3), 16) * (1 - amount));
  const g = Math.round(parseInt(hex.slice(3, 5), 16) * (1 - amount));
  const b = Math.round(parseInt(hex.slice(5, 7), 16) * (1 - amount));
  return `rgb(${r},${g},${b})`;
}

export function TowAgv({
  theta,
  color,
  isSelected,
  carried,
  status,
}: AgvSilhouetteProps) {
  const headingDeg = (theta * 180) / Math.PI;
  const strokeColor = isSelected ? color : darkenHex(color, 0.30);
  const strokeWidth = isSelected ? 2.5 : 1.5;
  const hitchColor = darkenHex(color, 0.45);

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

        {/* Main chassis body — compact */}
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

        {/* Towing hitch — centred on the rear face (−x edge) */}
        <Rect
          x={-BODY_W / 2 - HITCH_W}
          y={-HITCH_H / 2}
          width={HITCH_W}
          height={HITCH_H}
          cornerRadius={1}
          fill={hitchColor}
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

      {/* Status dot — world-space (not rotated), pulsing */}
      <StatusDot
        x={BODY_W / 2 - DOT_RADIUS}
        y={BODY_H / 2 + 5 + DOT_RADIUS}
        status={status}
      />
    </>
  );
}
