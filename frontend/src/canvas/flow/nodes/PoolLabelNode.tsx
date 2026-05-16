// Pool label pill — floats at pool center (top-edge of the pool stadium).
// The prototype places .pool-label at left:660px; top:410px with
// translate(-50%,-50%), so the pool top-center is the anchor.
// Like ZoneNode, this uses RF origin [0.5, 0.5] so position == center.

import { memo } from "react";
import type { NodeProps } from "@xyflow/react";
import type { TopologyPool } from "@/store/types";

export type PoolLabelNodeData = { pool: TopologyPool };

export const PoolLabelNode = memo(function PoolLabelNode({ data }: NodeProps) {
  const pool = (data as unknown as PoolLabelNodeData).pool;

  return (
    <div
      className="inline-flex select-none items-center gap-2 rounded-full border border-line bg-bg-canvas px-[9px] py-[4px] font-mono text-10.5 uppercase tracking-[0.06em] text-ink-3"
      style={{ whiteSpace: "nowrap" }}
    >
      <span className="font-semibold text-ink">{pool.label}</span>
      <span className="normal-case tracking-[0.02em] text-ink-4">{pool.sub}</span>
    </div>
  );
});
