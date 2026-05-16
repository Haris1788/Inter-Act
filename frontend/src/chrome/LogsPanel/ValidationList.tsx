import { cn } from "@/lib/cn";
import { useMemo } from "react";
import type { ValidationRow } from "@/store/types";
import { useTopologyValidation } from "@/store/derived";

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

export function ValidationList() {
  const { rows, errors, warnings } = useTopologyValidation();

  const sortedRows = useMemo(
    () =>
      [...rows].sort(
        (a, b) => SEVERITY_ORDER[a.level] - SEVERITY_ORDER[b.level],
      ),
    [rows],
  );

  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-3 py-2.5">
      <h4
        className={cn(
          "my-1 mb-2 flex items-center justify-between",
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

      {sortedRows.length === 0 ? (
        <p className="py-3 text-11.5 text-ink-5">No validation issues</p>
      ) : (
        sortedRows.map((row) => (
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
        ))
      )}
    </div>
  );
}
