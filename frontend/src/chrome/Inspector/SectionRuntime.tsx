import type { ActorRuntime } from "@/store/types";
import { Gauge } from "./Gauge";

const EM = "-";

interface SectionRuntimeProps {
  metrics: ActorRuntime["metrics"];
  live: boolean;
}

/** Placeholder gauge shown when live metrics are unavailable. */
function GaugePlaceholder({ label }: { label: string }) {
  return (
    <div>
      <div className="text-11 text-ink-4 mb-1">{label}</div>
      <div className="font-mono text-12 text-ink-5 mb-[5px]">{EM}</div>
      <div className="relative h-[7px] rounded-[4px] overflow-hidden bg-bg-sunken opacity-50" />
    </div>
  );
}

export function SectionRuntime({ metrics, live }: SectionRuntimeProps) {
  return (
    <div className="px-[14px] pt-[14px] pb-1">
      <div className="flex items-center justify-between mb-2">
        <span className="text-10.5 font-semibold tracking-[0.08em] uppercase text-ink-3">
          Runtime
        </span>
        {live ? (
          <span className="text-10.5 text-ink-5">not connected</span>
        ) : (
          <span className="font-mono text-10.5 text-ink-4">last 5m</span>
        )}
      </div>
      <div className="grid grid-cols-2 gap-x-[14px] gap-y-[10px]">
        {live ? (
          <>
            <GaugePlaceholder label="CPU" />
            <GaugePlaceholder label="Memory" />
            <GaugePlaceholder label="Battery" />
            <GaugePlaceholder label="Bus latency" />
          </>
        ) : (
          <>
            <Gauge
              label="CPU"
              value={`${metrics.cpu}%`}
              fillPct={metrics.cpu}
            />
            <Gauge
              label="Memory"
              value={`${metrics.memMb} MB`}
              fillPct={Math.round((metrics.memMb / 340) * 100)}
            />
            <Gauge
              label="Battery"
              value={`${metrics.battery}%`}
              fillPct={metrics.battery}
              okColor
            />
            <Gauge
              label="Bus latency"
              value={`${metrics.busLatencyMs} ms`}
              fillPct={Math.round((metrics.busLatencyMs / 50) * 100)}
            />
          </>
        )}
      </div>
    </div>
  );
}
