import { useState } from "react";
import { useSelectedActorBundle, useDataSource } from "@/store/selectors";
import { InspHead } from "./InspHead";
import { InspTabs, type Tab } from "./InspTabs";
import { SectionIdentity } from "./SectionIdentity";
import { SectionCapabilities } from "./SectionCapabilities";
import { SectionMotion } from "./SectionMotion";
import { SectionDocking } from "./SectionDocking";
import { SectionBrain } from "./SectionBrain";
import { SectionRuntime } from "./SectionRuntime";
import { SectionHistory } from "./SectionHistory";
import { SectionSpatial } from "./SectionSpatial";
import { ActionsRow } from "./ActionsRow";

/** Subtle uppercase band divider - separates authored config from operational runtime. */
function BandDivider({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-[8px] px-[14px] pt-[12px] pb-[2px]">
      <span className="text-10.5 font-semibold uppercase tracking-[0.08em] text-ink-4 select-none whitespace-nowrap">
        {label}
      </span>
      <span className="flex-1 h-px bg-line-soft" />
    </div>
  );
}

export function Inspector() {
  const dataSource = useDataSource();
  const live = dataSource === "live";

  // ONE authoritative subscription. Header / body / runtime / actions all
  // read from this single bundle, so the header can never lag the body when
  // selection changes (Phase 14 fix). Runtime falls back to the canonical
  // default → schema-driven rendering for actors with no live runtime yet
  // (Phase 13 invariant preserved).
  const { selectedId, actor, runtime } = useSelectedActorBundle();

  const [activeTab, setActiveTab] = useState<Tab>("Properties");

  if (!selectedId || !actor) {
    return (
      <aside className="flex h-full flex-col border-l border-line bg-bg-panel">
        <div className="flex-1 flex flex-col items-center justify-center gap-2 text-12 text-ink-4">
          <svg
            width="22"
            height="22"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            className="opacity-30"
          >
            <circle cx="12" cy="12" r="9" />
            <path d="M12 8v4l2 2" />
          </svg>
          <span>Select an actor</span>
        </div>
      </aside>
    );
  }

  return (
    <aside className="flex h-full flex-col border-l border-line bg-bg-panel min-h-0">
      <InspHead actor={actor} runtime={runtime} live={live} />
      <InspTabs active={activeTab} onChange={setActiveTab} />

      <div className="overflow-y-auto flex-1 py-1">
        {activeTab === "Properties" && (
          <>
            {/* ── Configuration band - authored / topology ── */}
            <BandDivider label="Configuration" />
            <SectionIdentity actor={actor} live={live} />
            <SectionSpatial actor={actor} />
            <SectionCapabilities actor={actor} live={live} />
            <SectionMotion actor={actor} live={live} />
            <SectionBrain actor={actor} live={live} />

            {/* ── Runtime & Docking band - operational ── */}
            <BandDivider label="Runtime & Docking" />
            <SectionDocking live={live} />
            <SectionRuntime metrics={runtime.metrics} live={live} />
          </>
        )}
        {activeTab === "Runtime" && (
          <div className="px-[14px] pt-[14px]">
            <SectionRuntime metrics={runtime.metrics} live={live} />
          </div>
        )}
        {activeTab === "History" && <SectionHistory actorId={selectedId} />}
        {activeTab === "YAML" && (
          <div className="px-[14px] pt-[14px] font-mono text-11 text-ink-3">
            {`id: ${actor.id}\ntype: ${actor.type}\nlabel: ${actor.label}`}
          </div>
        )}
      </div>

      <ActionsRow actorId={actor.id} />
    </aside>
  );
}
