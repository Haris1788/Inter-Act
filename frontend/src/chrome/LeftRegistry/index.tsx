import { useState } from "react";
import { useActors } from "@/store/selectors";
import { useStore } from "@/store";
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

// ── Actor glyph ──────────────────────────────────────────────────────────────

const ActorDot = (
  <svg width="8" height="8" viewBox="0 0 8 8">
    <circle cx="4" cy="4" r="3.5" fill="currentColor" />
  </svg>
);

// ── Palette data (for filtering) ─────────────────────────────────────────────

interface ChipItem {
  kind: "chip";
  name: string;
  sub: string;
  paletteKey: string;
  icon: React.ReactNode;
}

interface RowItem {
  kind: "row";
  name: string;
  meta: string;
  icon: React.ReactNode;
}

interface CapItem {
  kind: "cap";
  name: string;
  count: string;
  filled: boolean;
}

type PaletteItem = ChipItem | RowItem | CapItem;

interface PaletteSection {
  label: string;
  grid?: boolean;
  listClassName?: string;
  items: PaletteItem[];
}

const PALETTE: PaletteSection[] = [
  {
    label: "Zones",
    grid: true,
    items: [
      { kind: "chip", name: "Source feed",    sub: "input · inbound",  paletteKey: "zone-source", icon: ZoneSource },
      { kind: "chip", name: "Sink / outflow", sub: "output · outbound", paletteKey: "zone-sink",   icon: ZoneSink },
    ],
  },
  {
    label: "Capability Stations",
    grid: true,
    items: [
      { kind: "chip", name: "Arm · 6DoF", sub: "pick · place · dock", paletteKey: "arm-6dof", icon: CapStationArm6DoF },
      { kind: "chip", name: "Arm · SCARA", sub: "fast cycle", paletteKey: "arm-scara", icon: CapStationArmSCARA },
      { kind: "chip", name: "Arm · Delta", sub: "high-speed", paletteKey: "arm-delta", icon: CapStationArmDelta },
    ],
  },
  {
    label: "Mobile Agents",
    grid: true,
    items: [
      { kind: "chip", name: "Shuttle AGV", sub: "fast · cap 1", paletteKey: "agv-shuttle", icon: MobileShuttle },
      { kind: "chip", name: "Forklift AGV", sub: "heavy · pallet", paletteKey: "agv-forklift", icon: MobileForklift },
      { kind: "chip", name: "Tow AGV", sub: "balanced", paletteKey: "agv-tow", icon: MobileTow },
    ],
  },
  {
    label: "Capabilities",
    listClassName: "gap-0",
    items: [
      { kind: "cap", name: "pick",    count: "2 stations", filled: true },
      { kind: "cap", name: "place",   count: "2 stations", filled: true },
      { kind: "cap", name: "dock",    count: "5 actors",   filled: true },
      { kind: "cap", name: "transit", count: "3 agents",   filled: true },
      { kind: "cap", name: "weigh",   count: "0 actors",   filled: false },
      { kind: "cap", name: "inspect", count: "0 actors",   filled: false },
    ],
  },
];

// ── Component ────────────────────────────────────────────────────────────────

export function LeftRegistry() {
  const [query, setQuery] = useState("");

  const actors = useActors();
  const setSelectedActor = useStore((s) => s.setSelectedActor);
  const requestFocus = useStore((s) => s.requestFocus);

  const q = query.trim().toLowerCase();
  const isSearching = q.length > 0;

  // ── Filtered palette sections ────────────────────────────────────────────
  const filteredSections = PALETTE.map((section) => {
    const items = isSearching
      ? section.items.filter((item) => item.name.toLowerCase().includes(q))
      : section.items;
    return { ...section, items };
  }).filter((section) => section.items.length > 0);

  // ── Matched actors (cap 8) ───────────────────────────────────────────────
  const actorList = isSearching
    ? Object.values(actors)
        .filter(
          (a) =>
            a.id.toLowerCase().includes(q) ||
            a.label.toLowerCase().includes(q) ||
            a.type.toLowerCase().includes(q),
        )
        .slice(0, 8)
    : [];

  return (
    <aside className="flex h-full flex-col border-r border-line bg-bg-panel">
      {/* .left-search */}
      <SearchBox value={query} onChange={setQuery} />

      {/* .left-scroll */}
      <div className="flex-1 overflow-y-auto px-2 pb-4 pt-2">

        {/* ── Actors results (only when searching) ── */}
        {isSearching && actorList.length > 0 && (
          <div className="mt-1">
            <div className="flex items-center justify-between px-2 pb-[6px] pt-[10px]">
              <span className="text-10.5 font-semibold uppercase tracking-[0.08em] text-ink-3">
                Actors
              </span>
              <span className="font-mono text-10.5 text-ink-5">{actorList.length}</span>
            </div>
            <div className="flex flex-col gap-1 p-0.5">
              {actorList.map((actor) => (
                <button
                  key={actor.id}
                  type="button"
                  className="flex w-full cursor-pointer items-center gap-[9px] rounded-md px-2 py-1.5 text-left hover:bg-bg-hover"
                  onClick={() => {
                    setSelectedActor(actor.id);
                    requestFocus(actor.id);
                  }}
                >
                  <span className="inline-flex h-[22px] w-[22px] flex-shrink-0 items-center justify-center rounded-[5px] bg-bg-sunken text-ink-3">
                    {ActorDot}
                  </span>
                  <span className="flex-1 truncate text-12 text-ink">{actor.id}</span>
                  <span className="font-mono text-10.5 text-ink-4">{actor.type}</span>
                </button>
              ))}
            </div>
          </div>
        )}

        {/* ── Palette sections ── */}
        {filteredSections.map((section) => (
          <LibSection
            key={section.label}
            label={section.label}
            count={section.items.length}
            grid={section.grid}
            listClassName={section.listClassName}
          >
            {section.items.map((item) => {
              if (item.kind === "chip") {
                return (
                  <LibChip
                    key={item.paletteKey}
                    icon={item.icon}
                    name={item.name}
                    sub={item.sub}
                    paletteKey={item.paletteKey}
                  />
                );
              }
              if (item.kind === "row") {
                return (
                  <LibRow
                    key={item.name}
                    icon={item.icon}
                    name={item.name}
                    meta={item.meta}
                  />
                );
              }
              // cap
              return (
                <CapRow
                  key={item.name}
                  name={item.name}
                  count={item.count}
                  filled={item.filled}
                />
              );
            })}
          </LibSection>
        ))}

      </div>

      {/* .left-foot */}
      <LeftFoot />
    </aside>
  );
}
