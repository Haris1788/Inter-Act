// Node creation pipeline - palette descriptor → TopologyActor.
// Frontend authoring only; no backend involvement.

import type { TopologyActor, Vec2 } from "@/store/types";

export interface PaletteDescriptor {
  kind: "arm" | "agv";
  type: string;
  capabilities: string[];
  brain: string;
  spec: string;
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
  const size = d.kind === "arm" ? ARM_SIZE : AGV_SIZE;
  const id = d.kind === "arm" ? nextArmId(existing) : nextVehId(existing);
  return {
    id,
    kind: d.kind,
    type: d.type,
    label: id,
    position: { x: dropCenter.x - size.w / 2, y: dropCenter.y - size.h / 2 },
    size,
    capabilities: d.capabilities,
    liveCapability: null,
    brain: d.brain,
    spec: d.spec,
  };
}
