import { cn } from "@/lib/cn";

const TABS = [
  { id: "coordination", label: "Coordination", pill: "live" },
  { id: "fleet", label: "Fleet", pill: "5" },
  { id: "bus", label: "Message bus", pill: "429" },
  { id: "validation", label: "Validation", pill: "0" },
  { id: "events", label: "Events", pill: null },
] as const;

type TabId = (typeof TABS)[number]["id"];

interface LogsHeadProps {
  activeTab: TabId;
  onTab: (id: TabId) => void;
}

export function LogsHead({ activeTab, onTab }: LogsHeadProps) {
  return (
    <div
      className={cn(
        "flex h-9 shrink-0 items-center gap-1 border-b border-line-soft px-2.5",
      )}
    >
      {TABS.map((tab) => {
        const active = tab.id === activeTab;
        return (
          <button
            key={tab.id}
            onClick={() => onTab(tab.id)}
            className={cn(
              "inline-flex h-7 items-center gap-[7px] rounded-md px-3 text-12",
              active
                ? "border border-line bg-bg-surface text-ink"
                : "text-ink-3 hover:text-ink-2",
            )}
          >
            {tab.label}
            {tab.pill !== null && (
              <span
                className={cn(
                  "font-mono text-10 rounded px-[5px] py-[1px]",
                  active
                    ? "bg-ok-bg text-[#3D6A39]"
                    : "bg-bg-sunken text-ink-2",
                )}
              >
                {tab.pill}
              </span>
            )}
          </button>
        );
      })}

      {/* Spacer */}
      <span className="flex-1" />

      {/* Right tools */}
      <div className="flex items-center gap-1.5 text-ink-3">
        {/* Pill-running */}
        <span
          className={cn(
            "inline-flex items-center gap-1.5 rounded px-2 py-[3px]",
            "border bg-ok-bg text-11 text-[#3D6A39]",
            "border-[#CFD8C8]",
          )}
        >
          <span
            className="animate-pulse-dot inline-block h-1.5 w-1.5 rounded-full bg-ok"
          />
          staging · runtime #284
        </span>

        {/* Pause */}
        <button
          title="Pause"
          className="inline-flex h-[30px] w-[30px] items-center justify-center rounded-md hover:bg-bg-hover hover:text-ink"
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
            <rect x="6" y="5" width="4" height="14" />
            <rect x="14" y="5" width="4" height="14" />
          </svg>
        </button>

        {/* Filter */}
        <button
          title="Filter"
          className="inline-flex h-[30px] w-[30px] items-center justify-center rounded-md hover:bg-bg-hover hover:text-ink"
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
            <polygon points="3 4 21 4 14 13 14 19 10 21 10 13 3 4" />
          </svg>
        </button>

        {/* Clear */}
        <button
          title="Clear"
          className="inline-flex h-[30px] w-[30px] items-center justify-center rounded-md hover:bg-bg-hover hover:text-ink"
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
            <polyline points="3 6 5 6 21 6" />
            <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
          </svg>
        </button>

        {/* Collapse */}
        <button
          title="Collapse"
          className="inline-flex h-[30px] w-[30px] items-center justify-center rounded-md hover:bg-bg-hover hover:text-ink"
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
            <polyline points="6 9 12 15 18 9" />
          </svg>
        </button>
      </div>
    </div>
  );
}
