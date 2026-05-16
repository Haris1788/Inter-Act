// Fleet overview card — bottom-right placeholder area (replaces RF MiniMap).
// Renders a live fleet status list. Domain 6/7 can slot richer content here.
// Mirrors .fleet-card from styles-v2.css.

import { useActors } from "@/store/selectors";
import { useStore } from "@/store";
import { cn } from "@/lib/cn";
import type { ActorStatus } from "@/store/types";

export function FleetCard() {
  const actors = useActors();
  const actorRuntime = useStore((s) => s.actorRuntime);

  const entries = Object.values(actors);

  return (
    <div
      className="absolute bottom-[14px] right-[14px] z-[5] w-[196px] rounded-[10px] border border-line bg-white p-[10px_11px_9px] shadow-e2"
      id="fleet-card"
    >
      <div className="mb-2 flex items-center justify-between">
        <span className="text-10 font-semibold uppercase tracking-[0.08em] text-ink-3">Fleet</span>
        <span className="font-mono text-10.5 text-ink-4">{entries.length} actors</span>
      </div>

      {entries.map((actor, i) => {
        const rt = actorRuntime[actor.id];
        const status: ActorStatus = rt?.status ?? "idle";
        const battery = rt?.metrics?.battery;
        const phase = rt?.phase ?? "";

        return (
          <div
            key={actor.id}
            className={cn(
              "grid items-center gap-2 py-1 text-11.5",
              "grid-cols-[8px_1fr_auto_auto]",
              i > 0 && "border-t border-dashed border-line-soft",
            )}
          >
            {/* status dot */}
            <StatusDot status={status} />
            {/* name */}
            <span className="font-medium text-ink">
              {actor.id}
              <span className="ml-1 font-mono text-10 text-ink-4">
                {actor.type.split("·")[1]?.trim() ?? actor.type}
              </span>
            </span>
            {/* phase/met */}
            <span className="font-mono text-10.5 text-ink-3">{phase}</span>
            {/* battery (agv only) */}
            {battery != null ? (
              <span className="inline-flex items-center gap-1 font-mono text-10 text-ink-3">
                <span
                  className="relative h-[5px] w-[22px] overflow-hidden rounded-[2px] bg-bg-sunken"
                >
                  <span
                    className="absolute inset-y-0 left-0 bg-ink"
                    style={{ width: `${battery}%` }}
                  />
                </span>
                {battery}
              </span>
            ) : (
              <span />
            )}
          </div>
        );
      })}
    </div>
  );
}

function StatusDot({ status }: { status: ActorStatus }) {
  const cls =
    status === "busy"
      ? "bg-ok shadow-[0_0_0_2px_#E6EBE0]"
      : status === "warn"
        ? "bg-warn shadow-[0_0_0_2px_#F0E6D0]"
        : status === "idle"
          ? "bg-idle shadow-[0_0_0_2px_#E7E6E2]"
          : "bg-ok shadow-[0_0_0_2px_#E6EBE0]";

  return <span className={cn("h-[7px] w-[7px] rounded-full", cls)} />;
}
