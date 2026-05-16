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
  useActors,
  useBusMessages,
  useDataSource,
  useLinkStatus,
} from "@/store/selectors";
import { useStore } from "@/store";
import { useTopologyValidation } from "@/store/derived";

type TabId = "coordination" | "fleet" | "bus" | "validation" | "events";

export function LogsPanel() {
  const [activeTab, setActiveTab] = useState<TabId>("coordination");
  const [paused, setPaused] = useState(false);
  const [collapsed, setCollapsed] = useState(false);

  // Real data for counts
  const logLines = useLogLines();
  const events = useEvents();
  const actors = useActors();
  const busMessages = useBusMessages();
  const dataSource = useDataSource();
  const linkStatus = useLinkStatus();
  const { errors, warnings } = useTopologyValidation();

  // For pausing: snapshot the log lines when pause is toggled on
  const pausedLinesRef = useRef(logLines);
  const pausedEventsRef = useRef(events);
  const pausedBusRef = useRef(busMessages);

  function handlePause() {
    if (!paused) {
      // Snapshot current state
      pausedLinesRef.current = logLines;
      pausedEventsRef.current = events;
      pausedBusRef.current = busMessages;
    }
    setPaused((p) => !p);
  }

  // Clear for Coordination tab (logLines via hydrateRuntime)
  const hydrateRuntime = useStore((s) => s.hydrateRuntime);

  function handleClear() {
    if (activeTab === "coordination") {
      hydrateRuntime({ logLines: [] });
      if (paused) {
        pausedLinesRef.current = [];
      }
    } else if (activeTab === "bus") {
      hydrateRuntime({ busMessages: [] });
      if (paused) {
        pausedBusRef.current = [];
      }
    }
  }

  // Clear is only available for coordination and bus tabs
  const clearHandler: (() => void) | null =
    activeTab === "coordination" || activeTab === "bus" ? handleClear : null;

  // Effective data (frozen when paused)
  const displayLines = paused ? pausedLinesRef.current : logLines;
  const displayEvents = paused ? pausedEventsRef.current : events;
  const displayBus = paused ? pausedBusRef.current : busMessages;

  // Real counts (always live — counts never freeze; only content does)
  const counts = {
    coordination: logLines.length,
    fleet: Object.keys(actors).length,
    bus: busMessages.length,
    validation: errors + warnings,
    events: events.length,
  };

  return (
    <section
      className={cn(
        "flex flex-col border-t border-line bg-bg-panel",
        "min-h-0",
      )}
    >
      <LogsHead
        activeTab={activeTab}
        onTab={setActiveTab}
        counts={counts}
        paused={paused}
        onPause={handlePause}
        onClear={clearHandler}
        collapsed={collapsed}
        onCollapse={() => setCollapsed((c) => !c)}
        dataSource={dataSource}
        linkStatus={linkStatus}
      />

      {!collapsed && (
        <TabBody
          activeTab={activeTab}
          paused={paused}
          displayLines={displayLines}
          displayEvents={displayEvents}
          displayBus={displayBus}
        />
      )}
    </section>
  );
}

// ── TabBody ───────────────────────────────────────────────────────────────────
// Separated so LogsPanel hook count stays fixed regardless of collapsed state.

interface TabBodyProps {
  activeTab: TabId;
  paused: boolean;
  displayLines: ReturnType<typeof useLogLines>;
  displayEvents: ReturnType<typeof useEvents>;
  displayBus: ReturnType<typeof useBusMessages>;
}

function TabBody({ activeTab, paused, displayLines, displayEvents, displayBus }: TabBodyProps) {
  // Coordination — full-width log stream (frozen when paused)
  if (activeTab === "coordination") {
    return (
      <div className="min-h-0 flex-1 flex flex-col relative">
        {paused && <PausedBanner />}
        <LogStream lines={displayLines} />
      </div>
    );
  }

  // Fleet — actor roster only
  if (activeTab === "fleet") {
    return (
      <div className="min-h-0 flex-1 flex flex-col">
        <FleetRoster />
      </div>
    );
  }

  // Message bus — bus traffic (frozen when paused)
  if (activeTab === "bus") {
    return (
      <div className="min-h-0 flex-1 flex flex-col relative">
        {paused && <PausedBanner />}
        <BusPane messages={displayBus} />
      </div>
    );
  }

  // Validation — topology/deploy validation only
  if (activeTab === "validation") {
    return (
      <div className="min-h-0 flex-1 flex flex-col">
        <ValidationList />
      </div>
    );
  }

  // Events — full operational history timeline (frozen when paused)
  return (
    <div className="min-h-0 flex-1 flex flex-col relative">
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
