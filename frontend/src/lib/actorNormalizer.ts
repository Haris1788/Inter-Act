// THE single canonical actor normalizer. Every actor-creation path (drag,
// preset, backend hydration, import, restore, duplicate) must pass through
// `normalizeActor` so display naming + schema defaults are derived in exactly
// one place. Runtime id stays `arm-7` / `veh-c`; display label is canonical
// (`Arm 7` / `Veh C`) unless an explicit authored label was supplied.
//
// Phase 5 Stage 2A: ActorSeed extended with reach_px / capacity / speed.
// normalizeActor fills defaults so old workspaces (missing these fields) load
// cleanly. Migration is purely additive — no field renamed or removed.

import type { TopologyActor, ActorKind } from "@/store/types";

const ZONE_SIZE = { w: 100, h: 100 };

const ARM_SIZE = { w: 210, h: 150 };
const AGV_SIZE = { w: 184, h: 118 };

// Phase 5 spatial defaults (same values used by backend Stage 1 topology model)
const DEFAULT_REACH_PX = 120;
const DEFAULT_CAPACITY = 1;
const DEFAULT_SPEED = 1.0;

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
  // ── Phase 5 spatial fields (optional in seed; defaults filled by normalizer) ──
  /** Arm reach radius in canvas pixels. Default 120. Kept on AGVs for symmetric shape. */
  reach_px?: number;
  /** Maximum simultaneous payload capacity. Default 1. */
  capacity?: number;
  /** Speed multiplier (0.5–3.0). Default 1.0. */
  speed?: number;
  // ── Phase 5.1 Stage 3: per-kind AGV silhouette ─────────────────────────
  /** AGV vehicle subtype. Ignored for arms. Default "shuttle".
   *  Zone subtype: "source" | "sink". */
  subtype?: "shuttle" | "forklift" | "tow" | "source" | "sink";
}

export function normalizeActor(seed: ActorSeed): TopologyActor {
  const isArm = seed.kind === "arm";
  const isZone = seed.kind === "zone";
  const authored =
    seed.label && seed.label.trim() && seed.label.trim() !== seed.id;
  return {
    id: seed.id,
    kind: seed.kind,
    type: seed.type ?? (isArm ? "Arm" : isZone ? "Zone" : "AGV"),
    label: authored ? seed.label!.trim() : canonicalLabel(seed.id),
    position: seed.position ?? { x: 0, y: 0 },
    size: seed.size ?? (isArm ? { ...ARM_SIZE } : isZone ? { ...ZONE_SIZE } : { ...AGV_SIZE }),
    capabilities:
      seed.capabilities ??
      (isArm ? ["pick", "place", "dock"] : isZone ? [] : ["transit", "dock", "carry"]),
    liveCapability: seed.liveCapability ?? null,
    brain: seed.brain ?? (isArm ? "Scripted" : isZone ? "—" : "Scheduler · FIFO"),
    spec: seed.spec ?? "",
    // Phase 5 spatial defaults — zones get 0 for reach/capacity/speed (semantic).
    reach_px: seed.reach_px ?? (isZone ? 0 : DEFAULT_REACH_PX),
    capacity: seed.capacity ?? (isZone ? 0 : DEFAULT_CAPACITY),
    speed: seed.speed ?? (isZone ? 0 : DEFAULT_SPEED),
    // Phase 5.1 Stage 3: default "shuttle" for AGVs; arms/zones carry undefined.
    subtype: seed.subtype ?? (isArm || isZone ? undefined : "shuttle"),
  };
}
