import { useState } from "react";
import { useActors } from "@/store/selectors";
import { useStore } from "@/store";
import { CapRow } from "./CapRow";
import { LeftFoot } from "./LeftFoot";
import { LibChip } from "./LibChip";
import { LibRow } from "./LibRow";
import { LibSection, LibGroupHeader } from "./LibSection";
import { SearchBox } from "./SearchBox";

// ── Capability Stations ──────────────────────────────────────────────────────

const CapStationArm6DoF = (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
    <circle cx="6" cy="18" r="2" />
    <path d="M6 16V9l6-3v5l5 1v6" />
    <circle cx="17" cy="17" r="2" />
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

// ── Zones ────────────────────────────────────────────────────────────────────
// US 34.1 (Sneh-port 5c23991, c228768): Control section deleted entirely
// (Scripted / Scheduler FIFO / LLM assist were never wired); Charging dock
// zone removed (no charging behaviour in the warehouse demo). Zone glyphs
// are now the only "row" items in the sidebar.

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
  /** US 34.1 (Sneh-port f8569c9): when set, a group header with this label
   *  is rendered once before the first section that carries the same group
   *  string. Sibling sections sharing the same group cluster under it. */
  group?: string;
  /** When true, the section header renders as a smaller indented subsection. */
  sub?: boolean;
}

// Sidebar now reflects ONLY what the live system actually has:
//   · Input Zone + Output Zone (real chips in the warehouse)
//   · Input Arm + Output Arm (the two actual fixed actors)
//   · AGV-1 / AGV-2 / AGV-3 (the three real fleet members)
//   · Capabilities table (real)
// Removed: SCARA/Delta arm variants, Shuttle/Forklift/Tow AGV variants —
// these were palette decoration from the prototype and never represented
// real actors. Drag-drop of "new actor types" is also disabled at runtime,
// so showing variants the user can't deploy was actively misleading.
const PALETTE: PaletteSection[] = [
  {
    label: "Zones",
    grid: true,
    items: [
      { kind: "chip", name: "Input Zone",  sub: "input · inbound",   paletteKey: "zone-source", icon: ZoneSource },
      { kind: "chip", name: "Output Zone", sub: "output · outbound", paletteKey: "zone-sink",   icon: ZoneSink },
    ],
  },
  {
    label: "Stationary Actors",
    group: "Actors",
    sub: true,
    grid: true,
    items: [
      { kind: "chip", name: "Input Arm",  sub: "loads AGVs",   paletteKey: "arm-input",  icon: CapStationArm6DoF },
      { kind: "chip", name: "Output Arm", sub: "unloads AGVs", paletteKey: "arm-output", icon: CapStationArm6DoF },
    ],
  },
  {
    label: "Mobile Actors",
    group: "Actors",
    sub: true,
    grid: true,
    items: [
      { kind: "chip", name: "AGV 1", sub: "blue · 10–20 kg",   paletteKey: "agv-1", icon: MobileShuttle },
      { kind: "chip", name: "AGV 2", sub: "green · 20–50 kg",  paletteKey: "agv-2", icon: MobileShuttle },
      { kind: "chip", name: "AGV 3", sub: "yellow · 50–80 kg", paletteKey: "agv-3", icon: MobileShuttle },
    ],
  },
  {
    label: "Capabilities",
    listClassName: "gap-0",
    items: [
      { kind: "cap", name: "load",    count: "1 arm",   filled: true },
      { kind: "cap", name: "unload",  count: "1 arm",   filled: true },
      { kind: "cap", name: "transit", count: "3 AGVs",  filled: true },
      { kind: "cap", name: "colour gate", count: "3 weight bands", filled: true },
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

        {/* ── Palette sections (US 34.1: emit a LibGroupHeader once per
            unique `group` string, immediately before the first section that
            carries that group). ── */}
        {(() => {
          const rendered: React.ReactNode[] = [];
          let lastGroup: string | undefined;
          filteredSections.forEach((section) => {
            if (section.group && section.group !== lastGroup) {
              rendered.push(
                <LibGroupHeader key={`grp-${section.group}`} label={section.group} />,
              );
              lastGroup = section.group;
            }
            if (!section.group) lastGroup = undefined;
            rendered.push(
              <LibSection
                key={section.label}
                label={section.label}
                count={section.items.length}
                grid={section.grid}
                listClassName={section.listClassName}
                sub={section.sub}
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
              </LibSection>,
            );
          });
          return rendered;
        })()}

      </div>

      {/* .left-foot */}
      <LeftFoot />
    </aside>
  );
}
