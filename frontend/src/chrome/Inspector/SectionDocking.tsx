import { cn } from "@/lib/cn";
import { Field } from "./Field";

interface DockRow {
  name: string;
  dist: string;
  live: boolean;
  icon: React.ReactNode;
}

const DOCK_ROWS: DockRow[] = [
  {
    name: "arm-1 · pick station",
    dist: "approaching · 0.4 m",
    live: true,
    icon: (
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
        <circle cx="6" cy="18" r="2"/>
        <path d="M6 16V9l6-3v5l5 1v6"/>
      </svg>
    ),
  },
  {
    name: "arm-2 · place station",
    dist: "reachable · 7.2 m",
    live: false,
    icon: (
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
        <rect x="4" y="14" width="16" height="3" rx="1"/>
        <path d="M9 14V8h6v6"/>
      </svg>
    ),
  },
  {
    name: "charge-1 · slow lane",
    dist: "reachable · 3.1 m",
    live: false,
    icon: (
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7">
        <path d="M13 2L3 14h7l-1 8 10-12h-7l1-8z"/>
      </svg>
    ),
  },
];

export function SectionDocking() {
  return (
    <div className="px-[14px] pt-[14px] pb-1">
      <div className="flex items-center justify-between mb-2">
        <span className="text-10.5 font-semibold tracking-[0.08em] uppercase text-ink-3">Docking</span>
        <span className="text-11 text-ink-4">3 compatible</span>
      </div>

      <div className="flex flex-col gap-[5px]">
        {DOCK_ROWS.map((row) => (
          <div
            key={row.name}
            className={cn(
              "grid items-center gap-2 px-2 py-[6px] rounded-[7px] text-11.5",
              "grid-cols-[22px_1fr_auto]",
              row.live
                ? "border border-cta bg-white"
                : "bg-bg-surface border border-line-soft",
            )}
          >
            <span className="w-[22px] h-[22px] rounded-[5px] bg-bg-sunken text-ink inline-flex items-center justify-center">
              {row.icon}
            </span>
            <span className="text-ink font-medium">{row.name}</span>
            <span className={cn("font-mono text-10.5", row.live ? "text-cta font-semibold" : "text-ink-3")}>
              {row.dist}
            </span>
          </div>
        ))}
      </div>

      <div className="mt-2">
        <Field label="Dock precision">
          <div className="flex items-center h-[28px] px-2 bg-bg-surface border border-line rounded-sm gap-1">
            <input
              className="border-0 bg-transparent outline-none w-full text-12 text-ink font-mono"
              defaultValue="±20"
              readOnly
            />
            <span className="text-11 text-ink-4 font-mono ml-[6px]">mm</span>
          </div>
        </Field>
        <Field label="Approach speed">
          <div className="flex items-center h-[28px] px-2 bg-bg-surface border border-line rounded-sm gap-1">
            <input
              className="border-0 bg-transparent outline-none w-full text-12 text-ink font-mono"
              defaultValue="0.30"
              readOnly
            />
            <span className="text-11 text-ink-4 font-mono ml-[6px]">m/s</span>
          </div>
        </Field>
      </div>
    </div>
  );
}
