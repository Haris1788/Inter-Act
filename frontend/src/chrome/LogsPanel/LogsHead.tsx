import { cn } from "@/lib/cn";
import type { DataSource, LinkStatus } from "@/store/types";

type TabId = "coordination" | "bus" | "validation" | "events";

interface TabCounts {
  coordination: number;
  bus: number;
  validation: number;
  events: number;
}

interface LogsHeadProps {
  activeTab: TabId;
  onTab: (id: TabId) => void;
  counts: TabCounts;
  paused: boolean;
  onPause: () => void;
  onClear: (() => void) | null; // null = not available for this tab → button hidden
  collapsed: boolean;
  onCollapse: () => void;
  dataSource: DataSource;
  linkStatus: LinkStatus;
}

const TABS: { id: TabId; label: string }[] = [
  { id: "coordination", label: "Coordination" },
  { id: "bus", label: "Message bus" },
  { id: "validation", label: "Validation" },
  { id: "events", label: "Events" },
];

export function LogsHead({
  activeTab,
  onTab,
  counts,
  paused,
  onPause,
  onClear,
  collapsed,
  onCollapse,
  dataSource,
  linkStatus,
}: LogsHeadProps) {
  const linkOk = linkStatus === "connected";
  const linkWarn = linkStatus === "connecting";
  return (
    <div
      className={cn(
        "flex h-9 shrink-0 items-center gap-1 border-b border-line-soft px-2.5",
      )}
    >
      {TABS.map((tab) => {
        const active = tab.id === activeTab;
        const count = counts[tab.id];
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
            <span
              className={cn(
                "font-mono text-10 rounded px-[5px] py-[1px]",
                active
                  ? "bg-ok-bg text-[#3D6A39]"
                  : "bg-bg-sunken text-ink-2",
              )}
            >
              {count}
            </span>
          </button>
        );
      })}

      {/* Spacer */}
      <span className="flex-1" />

      {/* Right tools */}
      <div className="flex items-center gap-1.5 text-ink-3">
        {/* Runtime status pill — real data source + link health */}
        <span
          className={cn(
            "inline-flex items-center gap-1.5 rounded px-2 py-[3px] border text-11",
            linkOk
              ? "bg-ok-bg text-[#3D6A39] border-[#CFD8C8]"
              : linkWarn
                ? "bg-warn-bg text-warn border-[#E3D3A8]"
                : "bg-bg-sunken text-ink-3 border-line-soft",
          )}
          title={`data source: ${dataSource} · link: ${linkStatus}`}
        >
          <span
            className={cn(
              "inline-block h-1.5 w-1.5 rounded-full",
              linkOk
                ? "animate-pulse-dot bg-ok"
                : linkWarn
                  ? "bg-warn"
                  : "bg-err",
            )}
          />
          {dataSource} · {linkStatus}
        </span>

        {/* Pause — toggles frozen view */}
        <button
          title={paused ? "Resume" : "Pause"}
          onClick={onPause}
          className={cn(
            "inline-flex h-[30px] w-[30px] items-center justify-center rounded-md",
            "hover:bg-bg-hover hover:text-ink",
            paused && "bg-bg-hover text-ink",
          )}
        >
          {paused ? (
            /* Play icon when paused */
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
              <polygon points="5 3 19 12 5 21 5 3" />
            </svg>
          ) : (
            /* Pause icon when running */
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
              <rect x="6" y="5" width="4" height="14" />
              <rect x="14" y="5" width="4" height="14" />
            </svg>
          )}
        </button>

        {/* Clear — only shown when the active tab supports it */}
        {onClear !== null && (
          <button
            title="Clear"
            onClick={onClear}
            className="inline-flex h-[30px] w-[30px] items-center justify-center rounded-md hover:bg-bg-hover hover:text-ink"
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
              <polyline points="3 6 5 6 21 6" />
              <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
            </svg>
          </button>
        )}

        {/* Collapse */}
        <button
          title={collapsed ? "Expand" : "Collapse"}
          onClick={onCollapse}
          className="inline-flex h-[30px] w-[30px] items-center justify-center rounded-md hover:bg-bg-hover hover:text-ink"
        >
          <svg
            width="13"
            height="13"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            style={{ transform: collapsed ? "rotate(180deg)" : undefined }}
          >
            <polyline points="6 9 12 15 18 9" />
          </svg>
        </button>
      </div>
    </div>
  );
}
