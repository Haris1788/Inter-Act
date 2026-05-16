import { useActors, useValidation } from "@/store/selectors";
import { useStore } from "@/store";
import { cn } from "@/lib/cn";
import type { ActorStatus } from "@/store/types";

function statusDotClass(status: ActorStatus): string {
  switch (status) {
    case "ok":
      return "bg-ok";
    case "busy":
      return "bg-cta animate-pulse-dot";
    case "warn":
      return "bg-warn";
    case "idle":
      return "bg-idle";
    default:
      return "bg-idle";
  }
}

function statusDotShadow(status: ActorStatus): string {
  switch (status) {
    case "ok":
      return "0 0 0 2px #E6EBE0";
    case "busy":
      return "0 0 0 2px rgba(24,24,27,0.10)";
    case "warn":
      return "0 0 0 2px #F0E6D0";
    case "idle":
      return "0 0 0 2px #E7E6E2";
    default:
      return "0 0 0 2px #E7E6E2";
  }
}

export function CoordPane() {
  const actors = useActors();
  const validation = useValidation();
  const actorRuntime = useStore((s) => s.actorRuntime);

  const actorList = Object.values(actors);

  return (
    <div className="overflow-y-auto px-3 py-2.5">
      {/* Fleet roster */}
      <h4
        className={cn(
          "my-1 mb-2 flex items-center justify-between",
          "text-10.5 font-semibold uppercase tracking-[0.08em] text-ink-3",
        )}
      >
        Fleet roster
        <span className="font-mono text-10.5 normal-case tracking-normal text-ink-5">
          {actorList.length} actors
        </span>
      </h4>

      {actorList.map((actor) => {
        const rt = actorRuntime[actor.id];
        const status: ActorStatus = rt?.status ?? "idle";
        const phase = rt?.phase ?? "idle";
        const cpu = rt?.metrics?.cpu ?? 0;

        return (
          <div
            key={actor.id}
            className={cn(
              "grid items-center gap-2 border-b border-dashed border-line-soft",
              "px-1 py-1.5 text-11.5",
              "last:border-b-0",
            )}
            style={{ gridTemplateColumns: "18px 1fr 70px 56px" }}
          >
            {/* Status dot */}
            <span className="flex justify-center">
              <span
                className={cn(
                  "inline-block h-[7px] w-[7px] rounded-full",
                  statusDotClass(status),
                )}
                style={{ boxShadow: statusDotShadow(status) }}
              />
            </span>

            {/* Name + sub */}
            <span className="text-ink">
              {actor.id}
              <span className="ml-1 font-mono text-10.5 text-ink-4">
                · {actor.type}
              </span>
            </span>

            {/* State */}
            <span className="font-mono text-10.5 text-ink-3">{phase}</span>

            {/* Metric */}
            <span className="text-right font-mono text-10.5 text-ink-3">
              {cpu}% cpu
            </span>
          </div>
        );
      })}

      {/* Validation */}
      {validation.length > 0 && (
        <>
          <h4
            className={cn(
              "mb-2 mt-3.5 flex items-center justify-between",
              "text-10.5 font-semibold uppercase tracking-[0.08em] text-ink-3",
            )}
          >
            Validation
            <span className="font-mono text-10.5 normal-case tracking-normal text-ink-5">
              {validation.filter((v) => v.level === "warn").length} errors ·{" "}
              {validation.filter((v) => v.level === "info").length} hints
            </span>
          </h4>

          {validation.map((row) => (
            <div
              key={row.id}
              className="flex items-center gap-2 py-[5px] text-12 text-ink-2"
            >
              <span
                className={cn(
                  row.level === "ok" && "text-ok",
                  row.level === "info" && "text-info",
                  row.level === "warn" && "text-warn",
                )}
              >
                {row.level === "ok" ? "✓" : "i"}
              </span>
              <span>{row.text}</span>
            </div>
          ))}
        </>
      )}
    </div>
  );
}
