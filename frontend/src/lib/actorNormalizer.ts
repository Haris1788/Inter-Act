// THE single canonical actor normalizer. Every actor-creation path (drag,
// preset, backend hydration, import, restore, duplicate) must pass through
// `normalizeActor` so display naming + schema defaults are derived in exactly
// one place. Runtime id stays `arm-7` / `veh-c`; display label is canonical
// (`Arm 7` / `Veh C`) unless an explicit authored label was supplied.

import type { TopologyActor, ActorKind } from "@/store/types";

const ARM_SIZE = { w: 210, h: 150 };
const AGV_SIZE = { w: 184, h: 118 };
// Phase 7 Stage A: zone box (E parity — E uses 100×100 for kind:"zone").
const ZONE_SIZE = { w: 100, h: 100 };

/** Title-case a runtime id: `arm-7` → `Arm 7`, `veh-a` → `Veh A`. */
export function canonicalLabel(id: string): string {
  return id
    .split("-")
    .map((seg) => seg.charAt(0).toUpperCase() + seg.slice(1))
    .join(" ");
}

export interface ActorSeed {
  id: string;
  kind: ActorKind;
  type?: string;
  /** explicit authored label; ignored when empty or equal to id */
  label?: string;
  position?: { x: number; y: number };
  size?: { w: number; h: number };
  capabilities?: string[];
  liveCapability?: string | null;
  brain?: string;
  spec?: string;
  /**
   * Explicit identity marker. Pass true only from drag-drop creation
   * (nodeFactory.createActorFromPalette). All other paths omit it or
   * pass false — seeded, hydrated, and preset actors get authored:false.
   * Absent in older saved workspaces → normalized to false (backward compat).
   */
  authored?: boolean;
  /**
   * Phase 7 Stage A: AGV silhouette or zone role. Ignored for arms.
   * Dormant this stage (no path seeds kind:"zone" yet); consumed by the
   * Stage C zone renderer + Stage E deploy. E parity.
   */
  subtype?: "shuttle" | "forklift" | "tow" | "source" | "sink";
}

export function normalizeActor(seed: ActorSeed): TopologyActor {
  const isArm = seed.kind === "arm";
  const isZone = seed.kind === "zone";
  const hasCustomLabel =
    seed.label && seed.label.trim() && seed.label.trim() !== seed.id;
  return {
    id: seed.id,
    kind: seed.kind,
    type: seed.type ?? (isArm ? "Arm" : isZone ? "Zone" : "AGV"),
    label: hasCustomLabel ? seed.label!.trim() : canonicalLabel(seed.id),
    position: seed.position ?? { x: 0, y: 0 },
    size:
      seed.size ??
      (isArm ? { ...ARM_SIZE } : isZone ? { ...ZONE_SIZE } : { ...AGV_SIZE }),
    capabilities:
      seed.capabilities ??
      (isArm
        ? ["pick", "place", "dock"]
        : isZone
          ? []
          : ["transit", "dock", "carry"]),
    liveCapability: seed.liveCapability ?? null,
    brain: seed.brain ?? (isArm ? "Scripted" : isZone ? "—" : "Scheduler · FIFO"),
    spec: seed.spec ?? "",
    authored: seed.authored === true,   // explicit boolean, never undefined
    // Phase 7 Stage A: default "shuttle" for AGVs; arms/zones carry undefined
    // unless explicitly seeded (zones get "source"/"sink" from nodeFactory in B).
    // E divergence: E drops `authored` (all invisible shells); D keeps it until
    // the Stage B card→shell flip, so it stays here.
    subtype: seed.subtype ?? (isArm || isZone ? undefined : "shuttle"),
  };
}
