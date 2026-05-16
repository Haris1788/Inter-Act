import { cn } from "@/lib/cn";
import type { TopologyActor } from "@/store/types";

interface Cap {
  label: string;
  on: boolean;
}

interface SectionCapabilitiesProps {
  actor: TopologyActor;
  live: boolean;
}

export function SectionCapabilities({ actor }: SectionCapabilitiesProps) {
  // Always derive capabilities from the actor's own topology data.
  const caps: Cap[] = actor.capabilities.map((c) => ({ label: c, on: true }));

  return (
    <div className="px-[14px] pt-[14px] pb-1">
      <div className="flex items-center justify-between mb-2">
        <span className="text-10.5 font-semibold tracking-[0.08em] uppercase text-ink-3">
          Capabilities
        </span>
        <span className="text-11 text-ink-4">
          {caps.length} declared
        </span>
      </div>
      {caps.length === 0 ? (
        <span className="text-11.5 text-ink-4">-</span>
      ) : (
        <div className="grid grid-cols-2 gap-x-2 gap-y-1">
          {caps.map((cap) => (
            <div
              key={cap.label}
              className={cn(
                "grid gap-[6px] items-center py-[2px]",
                "grid-cols-[14px_1fr]",
                cap.on ? "text-11.5 text-ink-2" : "text-11.5 text-ink-4",
              )}
            >
              <span
                className={cn(
                  "w-[10px] h-[10px] rounded-[2px] border border-ink ml-[2px]",
                  cap.on ? "bg-ink" : "bg-bg-surface",
                )}
              />
              {cap.label}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
