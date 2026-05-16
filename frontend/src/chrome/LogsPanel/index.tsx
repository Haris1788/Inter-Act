import { useState, useRef } from "react";
import { cn } from "@/lib/cn";
import { LogsHead } from "./LogsHead";
import { LogStream } from "./LogStream";
import { EventsPane } from "./EventsPane";
import { FleetRoster } from "./FleetRoster";
import { ValidationList } from "./ValidationList";
import { BusPane } from "./BusPane";
import {
  useLogLines,
  useEvents,
  useBusMessages,
  useDataSource,
  useLinkStatus,
  useLogsCollapsed,
  useLogsHeight,
} from "@/store/selectors";
import { useStore } from "@/store";
import { useTopologyValidation } from "@/store/derived";

// Phase 14: the dock is ONE docked operational workspace.
//   - left 65%  : stream tabs (Coordination / Message bus / Validation / Events)
//   - right 35% : permanent Fleet runtime sidebar (independent of tabs)
//   - collapse  : whole dock → slim header strip; canvas takes the space.
type TabId = "coordination" | "bus" | "validation" | "events";

const HEADER_H = 36; // matches LogsHead h-9

export function LogsPanel() {
  const [activeTab, setActiveTab] = useState<TabId>("coordination");
  const [paused, setPaused] = useState(false);

  const collapsed = useLogsCollapsed();
  const logsHeight = useLogsHeight();
  const toggleLogsCollapsed = useStore((s) => s.toggleLogsCollapsed);

  const logLines = useLogLines();
  const events = useEvents();
  const busMessages = useBusMessages();
  const dataSource = useDataSource();
  const linkStatus = useLinkStatus();
  const { errors, warnings } = useTopologyValidation();

  const pausedLinesRef = useRef(logLines);
  const pausedEventsRef = useRef(events);
  const pausedBusRef = useRef(busMessages);

  function handlePause() {
    if (!paused) {
      pausedLinesRef.current = logLines;
      pausedEventsRef.current = events;
      pausedBusRef.current = busMessages;
    }
    setPaused((p) => !p);
  }

  const hydrateRuntime = useStore((s) => s.hydrateRuntime);

  function handleClear() {
    if (activeTab === "coordination") {
      hydrateRuntime({ logLines: [] });
      if (paused) pausedLinesRef.current = [];
    } else if (activeTab === "bus") {
      hydrateRuntime({ busMessages: [] });
      if (paused) pausedBusRef.current = [];
    }
  }

  // Clear only meaningful for the live-buffer stream tabs.
  const clearHandler: (() => void) | null =
    activeTab === "coordination" || activeTab === "bus" ? handleClear : null;

  const displayLines = paused ? pausedLinesRef.current : logLines;
  const displayEvents = paused ? pausedEventsRef.current : events;
  const displayBus = paused ? pausedBusRef.current : busMessages;

  // Counts: left stream tabs only — Fleet is a permanent sidebar, not a tab.
  const counts = {
    coordination: logLines.length,
    bus: busMessages.length,
    validation: errors + warnings,
    events: events.length,
  };

  return (
    <section
      className={cn(
        "flex flex-col overflow-hidden border-t border-line bg-bg-panel",
        "min-h-0 transition-[height] duration-200 ease-out",
      )}
      style={{ height: collapsed ? HEADER_H : logsHeight }}
    >
      <LogsHead
        activeTab={activeTab}
        onTab={setActiveTab}
        counts={counts}
        paused={paused}
        onPause={handlePause}
        onClear={clearHandler}
        collapsed={collapsed}
        onCollapse={toggleLogsCollapsed}
        dataSource={dataSource}
        linkStatus={linkStatus}
      />

      {!collapsed && (
        <div className="flex min-h-0 flex-1">
          {/* LEFT 65% — operational stream workspace */}
          <div className="flex min-h-0 flex-1 flex-col border-r border-line basis-[65%]">
            <StreamBody
              activeTab={activeTab}
              paused={paused}
              displayLines={displayLines}
              displayEvents={displayEvents}
              displayBus={displayBus}
            />
          </div>

          {/* RIGHT 35% — permanent fleet runtime monitor */}
          <aside className="flex min-h-0 flex-col bg-bg-panel basis-[35%] shrink-0">
            <FleetRoster />
          </aside>
        </div>
      )}
    </section>
  );
}

interface StreamBodyProps {
  activeTab: TabId;
  paused: boolean;
  displayLines: ReturnType<typeof useLogLines>;
  displayEvents: ReturnType<typeof useEvents>;
  displayBus: ReturnType<typeof useBusMessages>;
}

function StreamBody({
  activeTab,
  paused,
  displayLines,
  displayEvents,
  displayBus,
}: StreamBodyProps) {
  if (activeTab === "coordination") {
    return (
      <div className="relative flex min-h-0 flex-1 flex-col">
        {paused && <PausedBanner />}
        <LogStream lines={displayLines} />
      </div>
    );
  }
  if (activeTab === "bus") {
    return (
      <div className="relative flex min-h-0 flex-1 flex-col">
        {paused && <PausedBanner />}
        <BusPane messages={displayBus} />
      </div>
    );
  }
  if (activeTab === "validation") {
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        <ValidationList />
      </div>
    );
  }
  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      {paused && <PausedBanner />}
      <EventsPane events={displayEvents} />
    </div>
  );
}

function PausedBanner() {
  return (
    <div className="absolute right-2 top-1 z-10 rounded bg-warn-bg px-2 py-[2px] font-mono text-[9.5px] text-warn pointer-events-none">
      Paused
    </div>
  );
}
