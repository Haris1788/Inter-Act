// Node creation pipeline - palette descriptor → TopologyActor.
// Frontend authoring only; no backend involvement. Phase 7 Stage F: every
// palette kind (arm/agv/zone) becomes a TopologyActor via createActorFromPalette.

import type { TopologyActor, Vec2 } from "@/store/types";
import { normalizeActor } from "@/lib/actorNormalizer";

export interface PaletteDescriptor {
  kind: "arm" | "agv" | "zone";
  type: string;
  capabilities: string[];
  brain: string;
  spec: string;
  /** zones only: maps to TopologyActor.subtype ("source" | "sink") */
  role?: "source" | "sink";
}

// dataTransfer key → descriptor. Keys are emitted by the sidebar drag source.
// Type-based authoring keys ("arm", "agv", "zone-source", "zone-sink") are the
// ones the sidebar emits; the older variant keys are retained as harmless
// aliases so any external caller/preset referencing them still resolves.
export const PALETTE: Record<string, PaletteDescriptor> = {
  arm: {
    kind: "arm",
    type: "Arm",
    capabilities: ["pick", "place", "dock"],
    brain: "Scripted",
    spec: "reach 1.2 m · 5 kg",
  },
  agv: {
    kind: "agv",
    type: "AGV",
    capabilities: ["transit", "dock", "carry"],
    brain: "Scheduler · FIFO",
    spec: "cap 1",
  },
  "zone-source": {
    kind: "zone",
    type: "Input Zone",
    capabilities: [],
    brain: "—",
    spec: "input · inbound",
    role: "source",
  },
  "zone-sink": {
    kind: "zone",
    type: "Output Zone",
    capabilities: [],
    brain: "—",
    spec: "output · outbound",
    role: "sink",
  },
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
  },
  "agv-forklift": {
    kind: "agv",
    type: "Forklift AGV",
    capabilities: ["transit", "dock", "carry"],
    brain: "Scheduler · FIFO",
    spec: "heavy · pallet",
  },
  "agv-tow": {
    kind: "agv",
    type: "Tow AGV",
    capabilities: ["transit", "dock", "carry"],
    brain: "Scheduler · FIFO",
    spec: "balanced",
  },
};

const ARM_SIZE = { w: 210, h: 150 };
const AGV_SIZE = { w: 184, h: 118 };
// Phase 7 Stage B: zones are now first-class actors (kind:"zone"). 100×100 box
// matches the normalizer + E's ZONE_SIZE.
const ZONE_SIZE = { w: 100, h: 100 };

function nextArmId(taken: Record<string, unknown>): string {
  let n = 1;
  while (taken[`arm-${n}`]) n++;
  return `arm-${n}`;
}
function nextVehId(taken: Record<string, unknown>): string {
  let n = 1;
  while (taken[`veh-${n}`]) n++;
  return `veh-${n}`;
}
function nextZoneId(role: "source" | "sink", taken: Record<string, unknown>): string {
  const base = role === "source" ? "zone-input" : "zone-output";
  let n = 1;
  while (taken[`${base}-${n}`]) n++;
  return `${base}-${n}`;
}

/**
 * Build a new actor for an arm/agv/zone palette key, centered on the drop
 * point (stage coords). Returns null only for an unknown key.
 *
 * Phase 7: every palette kind (arm/agv/zone) produces a TopologyActor through
 * this one path. PaletteDescriptor's `role` maps to TopologyActor.subtype for
 * zones (source/sink).
 */
export function createActorFromPalette(
  paletteKey: string,
  dropCenter: Vec2,
  existing: Record<string, unknown>,
): TopologyActor | null {
  const d = PALETTE[paletteKey];
  if (!d) return null;
  const isZone = d.kind === "zone";
  const size = d.kind === "arm" ? ARM_SIZE : isZone ? ZONE_SIZE : AGV_SIZE;
  const id =
    d.kind === "arm"
      ? nextArmId(existing)
      : isZone
        ? nextZoneId(d.role as "source" | "sink", existing)
        : nextVehId(existing);
  // No explicit label: the canonical normalizer derives "Arm 7" / "Zone Input 1"
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
    authored: true,   // drag-drop = user authored; transparent RF node, Konva only
    subtype: isZone ? (d.role as "source" | "sink") : undefined,
  });
}
