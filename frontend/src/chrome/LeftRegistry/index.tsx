import { CapRow } from "./CapRow";
import { LeftFoot } from "./LeftFoot";
import { LibChip } from "./LibChip";
import { LibRow } from "./LibRow";
import { LibSection } from "./LibSection";
import { SearchBox } from "./SearchBox";

// ── Capability Stations ──────────────────────────────────────────────────────

const CapStationArm6DoF = (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
    <circle cx="6" cy="18" r="2" />
    <path d="M6 16V9l6-3v5l5 1v6" />
    <circle cx="17" cy="17" r="2" />
  </svg>
);

const CapStationArmSCARA = (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
    <rect x="4" y="14" width="16" height="3" rx="1" />
    <path d="M9 14V8h6v6" />
    <path d="M12 8V4" />
  </svg>
);

const CapStationArmDelta = (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
    <path d="M4 6h16" />
    <path d="M6 6l6 12 6-12" />
    <circle cx="12" cy="18" r="1.5" />
  </svg>
);

// ── Mobile Agents ────────────────────────────────────────────────────────────

const MobileShuttle = (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
    <rect x="3" y="9" width="18" height="7" rx="1.5" />
    <circle cx="8" cy="18" r="1.7" />
    <circle cx="16" cy="18" r="1.7" />
    <path d="M5 9V7h14v2" />
  </svg>
);

const MobileForklift = (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
    <path d="M3 17h11V7h4l3 4v6h-2" />
    <circle cx="7" cy="19" r="1.5" />
    <circle cx="17" cy="19" r="1.5" />
  </svg>
);

const MobileTow = (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
    <rect x="3" y="11" width="11" height="5" rx="1" />
    <path d="M14 13h4l3 3" />
    <circle cx="7" cy="18" r="1.4" />
    <circle cx="18" cy="18" r="1.4" />
  </svg>
);

// ── Brains ───────────────────────────────────────────────────────────────────

const BrainScripted = (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
    <polyline points="6 9 12 15 18 9" />
    <polyline points="6 3 12 9 18 3" />
  </svg>
);

const BrainScheduler = (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
    <rect x="3" y="4" width="18" height="16" rx="2" />
    <line x1="3" y1="10" x2="21" y2="10" />
    <line x1="9" y1="4" x2="9" y2="20" />
  </svg>
);

const BrainLLM = (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
    <circle cx="12" cy="12" r="3" />
    <path d="M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M5 19l2-2M17 7l2-2" />
  </svg>
);

const BrainRL = (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
    <path d="M4 18l4-6 4 4 4-8 4 6" />
  </svg>
);

// ── Zones ────────────────────────────────────────────────────────────────────

const ZoneSource = (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
    <polyline points="5 12 12 5 19 12" />
    <line x1="12" y1="5" x2="12" y2="20" />
  </svg>
);

const ZoneSink = (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
    <polyline points="19 12 12 19 5 12" />
    <line x1="12" y1="4" x2="12" y2="19" />
  </svg>
);

const ZoneCharging = (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
    <path d="M13 2L3 14h7l-1 8 10-12h-7l1-8z" />
  </svg>
);

// ── Component ────────────────────────────────────────────────────────────────

export function LeftRegistry() {
  return (
    <aside className="flex h-full flex-col border-r border-line bg-bg-panel">
      {/* .left-search */}
      <SearchBox />

      {/* .left-scroll */}
      <div className="flex-1 overflow-y-auto px-2 pb-4 pt-2">

        {/* Capability Stations — 2-col grid */}
        <LibSection label="Capability Stations" count={3} grid>
          <LibChip icon={CapStationArm6DoF} name="Arm · 6DoF" sub="pick · place · dock" />
          <LibChip icon={CapStationArmSCARA} name="Arm · SCARA" sub="fast cycle" />
          <LibChip icon={CapStationArmDelta} name="Arm · Delta" sub="high-speed" />
        </LibSection>

        {/* Mobile Agents — 2-col grid */}
        <LibSection label="Mobile Agents" count={3} grid>
          <LibChip icon={MobileShuttle} name="Shuttle AGV" sub="fast · cap 1" />
          <LibChip icon={MobileForklift} name="Forklift AGV" sub="heavy · pallet" />
          <LibChip icon={MobileTow} name="Tow AGV" sub="balanced" />
        </LibSection>

        {/* Capabilities — list, gap:0 */}
        <LibSection label="Capabilities" count={6} listClassName="gap-0">
          <CapRow name="pick"    count="2 stations" filled />
          <CapRow name="place"   count="2 stations" filled />
          <CapRow name="dock"    count="5 actors"   filled />
          <CapRow name="transit" count="3 agents"   filled />
          <CapRow name="weigh"   count="0 actors"   filled={false} />
          <CapRow name="inspect" count="0 actors"   filled={false} />
        </LibSection>

        {/* Brains */}
        <LibSection label="Brains" count={4}>
          <LibRow icon={BrainScripted}  name="Scripted"        meta="default" />
          <LibRow icon={BrainScheduler} name="Scheduler"       meta="FIFO"    />
          <LibRow icon={BrainLLM}       name="LLM coordinator" meta="qwen"    />
          <LibRow icon={BrainRL}        name="RL policy"       meta="v3"      />
        </LibSection>

        {/* Zones */}
        <LibSection label="Zones" count={3}>
          <LibRow icon={ZoneSource}  name="Source feed"   meta="I"  />
          <LibRow icon={ZoneSink}    name="Sink / outflow" meta="O" />
          <LibRow icon={ZoneCharging} name="Charging dock" meta="⚡" />
        </LibSection>

      </div>

      {/* .left-foot */}
      <LeftFoot />
    </aside>
  );
}
