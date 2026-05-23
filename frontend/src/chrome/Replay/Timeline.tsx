// Timeline.tsx — horizontal lane view of bus history grouped by (task_id, round).
// Pure SVG. Read-only: clicking a message fires onSelect(index); no mutations.
import { useMemo, useRef, useState } from "react";
import type { BusMessage } from "@/store/types";
import { KIND_LABELS, KIND_TONE_HEX } from "@/chrome/LogsPanel/busMessageChip";

interface TimelineProps {
  messages: BusMessage[];
  cursorIndex: number;
  onSelect: (index: number) => void;
}

// --- Layout constants ---
const LANE_H = 28;       // px height of each lane row
const LANE_PAD = 4;      // vertical padding inside lane
const LEFT_W = 140;      // width of the left label column
const DOT_R = 5;         // radius of event dots
const MARKER_H = 20;     // height of special marker lines
const MIN_WIDTH = 600;   // minimum svg content width

interface Lane {
  key: string;
  taskId: string;
  round: number;
  indices: number[];    // indices into messages array for this lane
}

/** Parse a ts string like "14:32:01" → seconds since midnight, or 0 if unparseable. */
function tsToSec(ts: string): number {
  const parts = ts.split(":").map(Number);
  if (parts.length !== 3 || parts.some(isNaN)) return 0;
  return (parts[0] ?? 0) * 3600 + (parts[1] ?? 0) * 60 + (parts[2] ?? 0);
}

/** Group messages into lanes keyed by (task_id, round). Messages without a
 *  task_id are placed in a synthetic "system · r0" lane. */
function buildLanes(messages: BusMessage[]): Lane[] {
  const map = new Map<string, Lane>();
  messages.forEach((msg, idx) => {
    const taskId = msg.task_id ?? "system";
    const round = msg.round ?? 0;
    const key = `${taskId}::${round}`;
    if (!map.has(key)) {
      map.set(key, { key, taskId, round, indices: [] });
    }
    map.get(key)!.indices.push(idx);
  });
  return Array.from(map.values());
}

export function Timeline({ messages, cursorIndex, onSelect }: TimelineProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);

  const lanes = useMemo(() => buildLanes(messages), [messages]);

  // Compute time range across all messages (seconds).
  const { minSec, maxSec } = useMemo(() => {
    if (messages.length === 0) return { minSec: 0, maxSec: 1 };
    const secs = messages.map((m) => tsToSec(m.ts));
    const mn = Math.min(...secs);
    const mx = Math.max(...secs);
    // Ensure a non-zero range so we never divide by 0.
    return { minSec: mn, maxSec: mx === mn ? mn + 1 : mx };
  }, [messages]);

  if (messages.length === 0) return null;

  const svgH = Math.max(lanes.length * LANE_H + 8, 60);
  const drawW = MIN_WIDTH - LEFT_W;

  /** Map a message's ts to an x offset within the draw area [0, drawW]. */
  function xOf(msg: BusMessage): number {
    const s = tsToSec(msg.ts);
    const frac = (s - minSec) / (maxSec - minSec);
    return frac * (drawW - DOT_R * 2) + DOT_R;
  }

  return (
    <div
      ref={containerRef}
      className="overflow-x-auto overflow-y-auto"
      style={{ maxHeight: 260 }}
    >
      <svg
        width={MIN_WIDTH}
        height={svgH}
        style={{ display: "block", fontFamily: "monospace", fontSize: 10 }}
        role="img"
        aria-label="Negotiation timeline"
      >
        {/* Background */}
        <rect width={MIN_WIDTH} height={svgH} fill="#fbfbfd" />

        {/* Left label column background */}
        <rect x={0} y={0} width={LEFT_W} height={svgH} fill="#F1EFE8" />

        {/* Lane rows */}
        {lanes.map((lane, laneIdx) => {
          const y = laneIdx * LANE_H;
          const midY = y + LANE_H / 2;

          return (
            <g key={lane.key}>
              {/* Lane separator */}
              <line
                x1={0}
                y1={y}
                x2={MIN_WIDTH}
                y2={y}
                stroke="#E5E3DD"
                strokeWidth={0.5}
              />

              {/* Lane label */}
              <text
                x={6}
                y={midY + 4}
                fill="#6B6B72"
                fontSize={9.5}
                fontFamily="monospace"
              >
                {lane.taskId === "system"
                  ? "system"
                  : `${lane.taskId.slice(0, 12)}${lane.taskId.length > 12 ? "…" : ""}`}
                {lane.round > 0 && (
                  <tspan fill="#BFBFC2"> r{lane.round}</tspan>
                )}
              </text>

              {/* Baseline axis inside draw area */}
              <line
                x1={LEFT_W}
                y1={midY}
                x2={LEFT_W + drawW}
                y2={midY}
                stroke="#E5E3DD"
                strokeWidth={0.5}
              />

              {/* Event dots */}
              {lane.indices.map((msgIdx) => {
                const msg = messages[msgIdx]!;
                const cx = LEFT_W + xOf(msg);
                const tone = KIND_TONE_HEX[msg.kind];
                const isConsensus = msg.kind === "consensus_reached";
                const isFail =
                  msg.kind === "task_failed" || msg.kind === "leg_halted";
                const isCursor = msgIdx === cursorIndex;
                const isHover = msgIdx === hoverIdx;

                return (
                  <g
                    key={msgIdx}
                    style={{ cursor: "pointer" }}
                    onClick={() => onSelect(msgIdx)}
                    onMouseEnter={() => setHoverIdx(msgIdx)}
                    onMouseLeave={() => setHoverIdx(null)}
                    role="button"
                    aria-label={`${KIND_LABELS[msg.kind]} from ${msg.from}`}
                  >
                    {/* Consensus: full-height vertical line */}
                    {isConsensus && (
                      <line
                        x1={cx}
                        y1={y + LANE_PAD}
                        x2={cx}
                        y2={y + LANE_H - LANE_PAD}
                        stroke={tone.stroke}
                        strokeWidth={2}
                        strokeDasharray="3,2"
                      />
                    )}

                    {/* Failure: red marker line */}
                    {isFail && (
                      <line
                        x1={cx}
                        y1={midY - MARKER_H / 2}
                        x2={cx}
                        y2={midY + MARKER_H / 2}
                        stroke="#A4503B"
                        strokeWidth={2}
                      />
                    )}

                    {/* Dot */}
                    <circle
                      cx={cx}
                      cy={midY}
                      r={isCursor ? DOT_R + 2 : isHover ? DOT_R + 1 : DOT_R}
                      fill={tone.fill}
                      stroke={isCursor ? "#18181B" : tone.stroke}
                      strokeWidth={isCursor ? 2 : 1}
                    />

                    {/* Tooltip on hover */}
                    {isHover && (
                      <g>
                        <rect
                          x={cx + 8}
                          y={midY - 14}
                          width={130}
                          height={18}
                          rx={3}
                          fill="#18181B"
                          opacity={0.82}
                        />
                        <text
                          x={cx + 14}
                          y={midY - 1}
                          fill="#fbfbfd"
                          fontSize={9}
                          fontFamily="monospace"
                        >
                          {msg.ts} · {KIND_LABELS[msg.kind]} · {msg.from}
                        </text>
                      </g>
                    )}
                  </g>
                );
              })}
            </g>
          );
        })}

        {/* Bottom border */}
        <line
          x1={0}
          y1={svgH - 1}
          x2={MIN_WIDTH}
          y2={svgH - 1}
          stroke="#E5E3DD"
          strokeWidth={0.5}
        />

        {/* Vertical divider between label column and draw area */}
        <line
          x1={LEFT_W}
          y1={0}
          x2={LEFT_W}
          y2={svgH}
          stroke="#E5E3DD"
          strokeWidth={1}
        />
      </svg>
    </div>
  );
}
