import { useActors } from "@/store/selectors";
import { useStore } from "@/store";
import { cn } from "@/lib/cn";
import { useMemo } from "react";
import type { ActorStatus, ValidationRow } from "@/store/types";
import { useTopologyValidation } from "@/store/derived";

/** Lower index = rendered first (attention-worthy at top). */
const STATUS_SEVERITY: Record<ActorStatus, number> = {
  down: 0,
  warn: 1,
  busy: 2,
  ok: 3,
  idle: 4,
};

function statusDotClass(status: ActorStatus): string {
  switch (status) {
    case "ok":
      return "bg-ok";
    case "busy":
      return "bg-cta animate-pulse-dot";
    case "warn":
      return "bg-warn";
    case "down":
      return "bg-err";
    case "idle":
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
    case "down":
      return "0 0 0 2px #F5E6E6";
    case "idle":
      return "0 0 0 2px #E7E6E2";
  }
}

const SEVERITY_ORDER: Record<ValidationRow["level"], number> = {
  error: 0,
  warn: 1,
  info: 2,
  ok: 3,
};

function levelMarker(level: ValidationRow["level"]): string {
  if (level === "ok") return "✓";
  if (level === "error") return "✕";
  if (level === "warn") return "!";
  return "i";
}

export function CoordPane() {
  const actors = useActors();
  const { rows, errors, warnings } = useTopologyValidation();
  const actorRuntime = useStore((s) => s.actorRuntime);

  const sortedRows = useMemo(
    () =>
      [...rows].sort(
        (a, b) => SEVERITY_ORDER[a.level] - SEVERITY_ORDER[b.level],
      ),
    [rows],
  );

  const actorList = useMemo(() => {
    const list = Object.values(actors);
    return [...list].sort((a, b) => {
      const sa: ActorStatus = actorRuntime[a.id]?.status ?? "idle";
      const sb: ActorStatus = actorRuntime[b.id]?.status ?? "idle";
      return STATUS_SEVERITY[sa] - STATUS_SEVERITY[sb];
    });
  }, [actors, actorRuntime]);

  const degradedCount = useMemo(
    () =>
      actorList.filter((a) => {
        const s: ActorStatus = actorRuntime[a.id]?.status ?? "idle";
        return s === "down" || s === "warn";
      }).length,
    [actorList, actorRuntime],
  );

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
          {degradedCount > 0 && (
            <span className="ml-1.5 text-ink-4">
              · {degradedCount} degraded
            </span>
          )}
        </span>
      </h4>

      {actorList.map((actor) => {
        const rt = actorRuntime[actor.id];
        const status: ActorStatus = rt?.status ?? "idle";
        const phase = rt?.phase ?? "";
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
            <span
              className={cn(
                "font-mono text-10.5",
                status === "down" && !phase ? "text-ink-5" : "text-ink-3",
              )}
            >
              {phase || (status === "down" ? "offline" : "-")}
            </span>

            {/* Metric */}
            <span className="text-right font-mono text-10.5 text-ink-3">
              {cpu}% cpu
            </span>
          </div>
        );
      })}

      {/* Validation */}
      {sortedRows.length > 0 && (
        <>
          <h4
            className={cn(
              "mb-2 mt-3.5 flex items-center justify-between",
              "text-10.5 font-semibold uppercase tracking-[0.08em]",
              errors > 0 ? "text-err" : "text-ink-3",
            )}
          >
            Validation
            <span
              className={cn(
                "font-mono text-10.5 normal-case tracking-normal",
                errors > 0 ? "text-err" : "text-ink-5",
              )}
            >
              {errors} error{errors !== 1 ? "s" : ""} · {warnings} hint
              {warnings !== 1 ? "s" : ""}
            </span>
          </h4>

          {sortedRows.map((row) => (
            <div
              key={row.id}
              className="flex items-center gap-2 py-[5px] text-12 text-ink-2"
            >
              <span
                className={cn(
                  "font-mono text-10.5 w-3 shrink-0 text-center",
                  row.level === "ok" && "text-ok",
                  row.level === "info" && "text-info",
                  row.level === "warn" && "text-warn",
                  row.level === "error" && "text-err",
                )}
              >
                {levelMarker(row.level)}
              </span>
              <span>{row.text}</span>
            </div>
          ))}
        </>
      )}
    </div>
  );
}
