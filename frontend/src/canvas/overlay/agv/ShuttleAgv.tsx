// ShuttleAgv — low-profile body (50×30 px).
// Status indicator: pulsing colored dot + actor id text rendered inline below chassis.

import { Group, Rect, RegularPolygon, Line, Circle, Text } from "react-konva";
import type { AgvSilhouetteProps } from "./types";
import { useStatusPulse } from "./useStatusPulse";

const BODY_W = 50;
const BODY_H = 30;
const POINTER_SIZE = 10;

// Inline label geometry
const DOT_GAP = 4;          // px gap between dot right edge and text left edge
const FONT_SIZE = 9;
const LABEL_Y_OFFSET = 6;   // px below bottom edge of chassis
const CHARS_PER_PX = 5.5;   // rough char width estimate at font-size 9

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
  actorId,
}: AgvSilhouetteProps) {
  const headingDeg = (theta * 180) / Math.PI;
  const strokeColor = isSelected ? color : darkenHex(color, 0.30);
  const strokeWidth = isSelected ? 2.5 : 1.5;

  // Inline pulsing dot
  const pulse = useStatusPulse(status);
  const dotR = pulse.radius;

  // Center the [dot + gap + text] line horizontally under chassis
  const textWidth = actorId.length * CHARS_PER_PX;
  const totalW = dotR * 2 + DOT_GAP + textWidth;
  const lineStartX = -totalW / 2;
  const dotCX = lineStartX + dotR;
  const textX = lineStartX + dotR * 2 + DOT_GAP;
  const labelY = BODY_H / 2 + LABEL_Y_OFFSET;

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

      {/* Inline label: [pulsing dot] [actor id text] — world-space (not rotated) */}
      <Circle
        x={dotCX}
        y={labelY + FONT_SIZE / 2}
        radius={dotR}
        fill={pulse.fill}
        stroke="rgba(0,0,0,0.35)"
        strokeWidth={0.8}
        opacity={pulse.opacity}
      />
      <Text
        x={textX}
        y={labelY}
        text={actorId}
        fontSize={FONT_SIZE}
        fill="rgba(0,0,0,0.85)"
        fontStyle="600"
      />
    </>
  );
}
