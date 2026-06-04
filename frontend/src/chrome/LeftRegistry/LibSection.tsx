interface LibSectionProps {
  label: string;
  count: number;
  children: React.ReactNode;
  /** When true, use lib-grid (2-col grid) instead of lib-list (flex-col) */
  grid?: boolean;
  /** Extra className on the inner list/grid wrapper */
  listClassName?: string;
  /** US 34.1 (Sneh-port f8569c9): render as a subsection inside a group —
   *  smaller, indented header, tighter top margin. */
  sub?: boolean;
}

export function LibSection({ label, count, children, grid, listClassName, sub }: LibSectionProps) {
  return (
    <div className={sub ? "mt-0" : "mt-1"}>
      {/* .lib-head */}
      <div
        className={`flex items-center justify-between pb-[6px] ${
          sub ? "pl-3 pr-2 pt-[6px]" : "px-2 pt-[10px]"
        }`}
      >
        <span
          className={`font-semibold uppercase tracking-[0.08em] ${
            sub ? "text-[9.5px] text-ink-4" : "text-10.5 text-ink-3"
          }`}
        >
          {label}
        </span>
        <span className="font-mono text-10.5 text-ink-5">{count}</span>
      </div>

      {/* .lib-grid or .lib-list */}
      {grid ? (
        <div
          className={`grid gap-[6px] p-0.5 ${listClassName ?? ""}`}
          style={{ gridTemplateColumns: "1fr 1fr" }}
        >
          {children}
        </div>
      ) : (
        <div className={`flex flex-col gap-1 p-0.5 ${listClassName ?? ""}`}>
          {children}
        </div>
      )}
    </div>
  );
}

// US 34.1 (Sneh-port f8569c9): top-level group header rendered ONCE before
// the first subsection in a group. Used to bracket "Stationary Actors" +
// "Mobile Actors" under one "Actors" label without losing the visual cue
// of two separate palettes.
export function LibGroupHeader({ label }: { label: string }) {
  return (
    <div className="px-2 pb-[2px] pt-[12px]">
      <span className="text-10.5 font-semibold uppercase tracking-[0.08em] text-ink-3">
        {label}
      </span>
    </div>
  );
}
