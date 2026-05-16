import type { ActorRuntime } from "@/store/types";
import { Gauge } from "./Gauge";

interface SectionRuntimeProps {
  metrics: ActorRuntime["metrics"];
}

export function SectionRuntime({ metrics }: SectionRuntimeProps) {
  return (
    <div className="px-[14px] pt-[14px] pb-1">
      <div className="flex items-center justify-between mb-2">
        <span className="text-10.5 font-semibold tracking-[0.08em] uppercase text-ink-3">Runtime</span>
        <span className="font-mono text-10.5 text-ink-4">last 5m</span>
      </div>
      <div className="grid grid-cols-2 gap-x-[14px] gap-y-[10px]">
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
      </div>
    </div>
  );
}
