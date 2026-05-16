// Zone chip node (source / sink) — whisper chip centered at position.
// The prototype uses translate(-50%, -50%) on .zone-chip; since RF node
// positions are top-left, the mock data already stores the chip CENTER
// (per mockData.ts comments). We compensate by shifting the node anchor
// using the RF `origin` prop (set to [0.5, 0.5] in flowConfig nodeDefaults),
// so no manual offset is needed here.

import { memo } from "react";
import type { NodeProps } from "@xyflow/react";
import type { TopologyZone } from "@/store/types";

export type ZoneNodeData = TopologyZone;

function SourceIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <polyline points="5 12 12 5 19 12" />
      <line x1="12" y1="5" x2="12" y2="20" />
    </svg>
  );
}

function SinkIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <polyline points="19 12 12 19 5 12" />
      <line x1="12" y1="4" x2="12" y2="19" />
    </svg>
  );
}

export const ZoneNode = memo(function ZoneNode({ data }: NodeProps) {
  const zone = data as unknown as ZoneNodeData;

  return (
    <div className="flex flex-col items-center gap-[3px] select-none">
      {/* icon box */}
      <span
        className="inline-flex h-[36px] w-[36px] items-center justify-center rounded-[9px] border border-dashed border-line-strong bg-bg-sunken text-ink-2"
      >
        {zone.role === "source" ? <SourceIcon /> : <SinkIcon />}
      </span>
      {/* label */}
      <span className="font-mono text-10 uppercase tracking-[0.08em] text-ink-3">
        {zone.label}
      </span>
      {/* count */}
      <span className="font-mono text-10 text-ink-4">{zone.count}</span>
    </div>
  );
});
