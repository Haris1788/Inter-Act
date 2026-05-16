import { useState } from "react";
import { cn } from "@/lib/cn";
import { LogsHead } from "./LogsHead";
import { LogStream } from "./LogStream";
import { CoordPane } from "./CoordPane";

type TabId = "coordination" | "fleet" | "bus" | "validation" | "events";

export function LogsPanel() {
  const [activeTab, setActiveTab] = useState<TabId>("coordination");

  return (
    <section
      className={cn(
        "flex flex-col border-t border-line bg-bg-panel",
        "min-h-0",
      )}
    >
      <LogsHead activeTab={activeTab} onTab={setActiveTab} />

      {/* Body — 2-col grid: 1.5fr log-stream | 1fr coord-pane */}
      <div
        className="min-h-0 flex-1"
        style={{ display: "grid", gridTemplateColumns: "1.5fr 1fr" }}
      >
        <LogStream />
        <CoordPane />
      </div>
    </section>
  );
}
