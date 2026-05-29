// Node creation pipeline - palette descriptor → TopologyActor.
// Frontend authoring only; no backend involvement.

import type { TopologyActor, Vec2 } from "@/store/types";
import { normalizeActor } from "@/lib/actorNormalizer";

export interface PaletteDescriptor {
  kind: "arm" | "agv" | "zone";
  type: string;
  capabilities: string[];
  brain: string;
  spec: string;
  /** AGV vehicle subtype — carried through to TopologyActor.subtype. */
  subtype?: "shuttle" | "forklift" | "tow" | "source" | "sink";
}

// dataTransfer key → descriptor. Keys are emitted by the sidebar drag source.
export const PALETTE: Record<string, PaletteDescriptor> = {
  "arm-6dof": {
    kind: "arm",
    type: "Arm · 6DoF",
    capabilities: ["pick", "place", "dock"],
    brain: "Scripted",
    spec: "reach 1.2 m · 5 kg",
  },
  "arm-scara": {
    kind: "arm",
    type: "Arm · SCARA",
    capabilities: ["pick", "place", "dock"],
    brain: "Scripted",
    spec: "fast cycle",
  },
  "arm-delta": {
    kind: "arm",
    type: "Arm · Delta",
    capabilities: ["pick", "place", "dock"],
    brain: "Scripted",
    spec: "high-speed",
  },
  "agv-shuttle": {
    kind: "agv",
    type: "Shuttle AGV",
    capabilities: ["transit", "dock", "carry"],
    brain: "Scheduler · FIFO",
    spec: "fast · cap 1",
    subtype: "shuttle",
  },
  "agv-forklift": {
    kind: "agv",
    type: "Forklift AGV",
    capabilities: ["transit", "dock", "carry"],
    brain: "Scheduler · FIFO",
    spec: "heavy · pallet",
    subtype: "forklift",
  },
  "agv-tow": {
    kind: "agv",
    type: "Tow AGV",
    capabilities: ["transit", "dock", "carry"],
    brain: "Scheduler · FIFO",
    spec: "balanced",
    subtype: "tow",
  },
  "zone-source": {
    kind: "zone",
    type: "Input Zone",
    capabilities: [],
    brain: "—",
    spec: "input · inbound",
    subtype: "source",
  },
  "zone-sink": {
    kind: "zone",
    type: "Output Zone",
    capabilities: [],
    brain: "—",
    spec: "output · outbound",
    subtype: "sink",
  },
};

const ARM_SIZE = { w: 210, h: 150 };
const AGV_SIZE = { w: 184, h: 118 };
const ZONE_SIZE = { w: 100, h: 100 };

function nextArmId(taken: Record<string, unknown>): string {
  let n = 1;
  while (taken[`arm-${n}`]) n++;
  return `arm-${n}`;
}
function nextVehId(taken: Record<string, unknown>): string {
  for (let i = 0; i < 26; i++) {
    const id = `veh-${String.fromCharCode(97 + i)}`;
    if (!taken[id]) return id;
  }
  let n = 1;
  while (taken[`veh-${n}`]) n++;
  return `veh-${n}`;
}
function nextZoneId(subtype: "source" | "sink", taken: Record<string, unknown>): string {
  let n = 1;
  while (taken[`zone-${subtype}-${n}`]) n++;
  return `zone-${subtype}-${n}`;
}

/**
 * Build a new actor for a palette key, centered on the drop point (stage
 * coords). Returns null for an unknown key.
 */
export function createActorFromPalette(
  paletteKey: string,
  dropCenter: Vec2,
  existing: Record<string, unknown>,
): TopologyActor | null {
  const d = PALETTE[paletteKey];
  if (!d) return null;
  const size = d.kind === "arm" ? ARM_SIZE : d.kind === "zone" ? ZONE_SIZE : AGV_SIZE;
  let id: string;
  if (d.kind === "arm") {
    id = nextArmId(existing);
  } else if (d.kind === "zone") {
    id = nextZoneId(d.subtype as "source" | "sink", existing);
  } else {
    id = nextVehId(existing);
  }
  // No explicit label: the canonical normalizer derives "Arm 7" / "Zone Source 1"
  // from the id (same path presets/hydration use).
  return normalizeActor({
    id,
    kind: d.kind,
    type: d.type,
    position: { x: dropCenter.x - size.w / 2, y: dropCenter.y - size.h / 2 },
    size,
    capabilities: d.capabilities,
    brain: d.brain,
    spec: d.spec,
    subtype: d.subtype,
  });
}
