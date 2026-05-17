interface LibSectionProps {
  label: string;
  count: number;
  children: React.ReactNode;
  /** When true, use lib-grid (2-col grid) instead of lib-list (flex-col) */
  grid?: boolean;
  /** Extra className on the inner list/grid wrapper */
  listClassName?: string;
}

export function LibSection({ label, count, children, grid, listClassName }: LibSectionProps) {
  return (
    <div className="mt-1">
      {/* .lib-head */}
      <div className="flex items-center justify-between px-2 pb-[6px] pt-[10px]">
        <span className="text-10.5 font-semibold uppercase tracking-[0.08em] text-ink-3">
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
