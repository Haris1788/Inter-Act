import { useState } from "react";
import { cn } from "@/lib/cn";
import { LogsHead } from "./LogsHead";
import { LogStream } from "./LogStream";
import { CoordPane } from "./CoordPane";
import { EventsPane } from "./EventsPane";

type TabId = "coordination" | "fleet" | "bus" | "validation" | "events";

export function LogsPanel() {
  const [activeTab, setActiveTab] = useState<TabId>("coordination");

  const isEvents = activeTab === "events";

  return (
    <section
      className={cn(
        "flex flex-col border-t border-line bg-bg-panel",
        "min-h-0",
      )}
    >
      <LogsHead activeTab={activeTab} onTab={setActiveTab} />

      {isEvents ? (
        /* Events tab - full-width timeline */
        <div className="min-h-0 flex-1 flex flex-col">
          <EventsPane />
        </div>
      ) : (
        /* All other tabs - 2-col grid: 1.5fr log-stream | 1fr coord-pane */
        <div
          className="min-h-0 flex-1"
          style={{ display: "grid", gridTemplateColumns: "1.5fr 1fr" }}
        >
          <LogStream />
          <CoordPane />
        </div>
      )}
    </section>
  );
}
