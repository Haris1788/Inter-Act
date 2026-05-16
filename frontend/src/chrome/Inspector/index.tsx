import { useState } from "react";
import { useSelectedActorId, useActor, useActorRuntime } from "@/store/selectors";
import { InspHead } from "./InspHead";
import { InspTabs, type Tab } from "./InspTabs";
import { SectionIdentity } from "./SectionIdentity";
import { SectionCapabilities } from "./SectionCapabilities";
import { SectionMotion } from "./SectionMotion";
import { SectionDocking } from "./SectionDocking";
import { SectionBrain } from "./SectionBrain";
import { SectionTaskQueue } from "./SectionTaskQueue";
import { SectionRuntime } from "./SectionRuntime";
import { ActionsRow } from "./ActionsRow";

const FALLBACK_ID = "veh-1";

export function Inspector() {
  const selectedId = useSelectedActorId();
  const effectiveId = selectedId ?? FALLBACK_ID;

  const actor = useActor(effectiveId);
  const runtime = useActorRuntime(effectiveId);

  const [activeTab, setActiveTab] = useState<Tab>("Properties");

  if (!actor || !runtime) {
    return (
      <aside className="flex h-full flex-col border-l border-line bg-bg-panel">
        <div className="flex-1 flex items-center justify-center text-12 text-ink-4">
          No actor selected
        </div>
      </aside>
    );
  }

  return (
    <aside className="flex h-full flex-col border-l border-line bg-bg-panel min-h-0">
      <InspHead actor={actor} runtime={runtime} />
      <InspTabs active={activeTab} onChange={setActiveTab} />

      <div className="overflow-y-auto flex-1 py-1">
        {activeTab === "Properties" && (
          <>
            <SectionIdentity actor={actor} />
            <SectionCapabilities />
            <SectionMotion />
            <SectionDocking />
            <SectionBrain />
            <SectionTaskQueue taskQueue={runtime.taskQueue} />
            <SectionRuntime metrics={runtime.metrics} />
          </>
        )}
        {activeTab === "Runtime" && (
          <div className="px-[14px] pt-[14px]">
            <SectionRuntime metrics={runtime.metrics} />
          </div>
        )}
        {activeTab === "Logs" && (
          <div className="px-[14px] pt-[14px] text-12 text-ink-4">
            Log output will appear here.
          </div>
        )}
        {activeTab === "YAML" && (
          <div className="px-[14px] pt-[14px] font-mono text-11 text-ink-3">
            {`id: ${actor.id}\ntype: ${actor.type}\nlabel: ${actor.label}`}
          </div>
        )}
      </div>

      <ActionsRow />
    </aside>
  );
}
