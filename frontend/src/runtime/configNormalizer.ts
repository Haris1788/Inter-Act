// /config (backend geometry) → topology slice. Decentralised model:
// build ONLY from arms + vehicles + intake/output zones. handover_zones are
// intentionally ignored (no transfer pads in the new architecture).

import { STAGE_W, STAGE_H } from "@/lib/stageCoords";
import type { TopologyActor, TopologyZone, Vec2 } from "@/store/types";

interface RawGraphNode {
  id: string;
  type: "arm" | "vehicle";
  label?: string;
  upstream?: string[];
  downstream?: string[];
  geometry?: { pivot?: { x: number; y: number }; length?: number };
}
interface RawAsset {
  id: string;
  type: string;
  label?: string;
  geometry?: { waypoints?: { x: number; y: number }[]; speed?: number };
}
interface RawZone {
  x: number;
  y: number;
  width?: number;
  height?: number;
  label?: string;
}
export interface RawConfig {
  canvas_width?: number;
  canvas_height?: number;
  arms?: { id: number; pivot_x: number; pivot_y: number; length?: number; label?: string }[];
  assets?: RawAsset[];
  asset_graph?: RawGraphNode[];
  intake_zone?: RawZone;
  output_zone?: RawZone;
}

export interface NormalizedTopology {
  actors: Record<string, TopologyActor>;
  zones: Record<string, TopologyZone>;
  /** actorId → downstream actorIds (for AGV transit routing) */
  adjacency: Record<string, string[]>;
}

const ARM_SIZE = { w: 210, h: 150 };
const AGV_SIZE = { w: 184, h: 118 };

export function normalizeConfig(cfg: RawConfig): NormalizedTopology {
  const cw = cfg.canvas_width || 1320;
  const ch = cfg.canvas_height || 600;
  // Keep aspect; center the scaled backend canvas in the stage.
  const s = Math.min(STAGE_W / cw, STAGE_H / ch);
  const ox = (STAGE_W - cw * s) / 2;
  const oy = (STAGE_H - ch * s) / 2;
  const map = (p: Vec2): Vec2 => ({ x: ox + p.x * s, y: oy + p.y * s });

  const actors: Record<string, TopologyActor> = {};
  const zones: Record<string, TopologyZone> = {};
  const adjacency: Record<string, string[]> = {};

  const graph = cfg.asset_graph ?? [];
  const assetsById = new Map((cfg.assets ?? []).map((a) => [a.id, a]));
  const armPivot = new Map(
    (cfg.arms ?? []).map((a) => [`arm-${a.id}`, { x: a.pivot_x, y: a.pivot_y }]),
  );

  for (const n of graph) {
    adjacency[n.id] = n.downstream ?? [];
    if (n.type === "arm") {
      const pv = n.geometry?.pivot ?? armPivot.get(n.id) ?? { x: cw / 2, y: ch / 2 };
      const c = map(pv);
      actors[n.id] = {
        id: n.id,
        kind: "arm",
        type: n.label?.includes("·") ? n.label : "Arm",
        label: n.label ?? n.id,
        position: { x: c.x - ARM_SIZE.w / 2, y: c.y - ARM_SIZE.h / 2 },
        size: ARM_SIZE,
        capabilities: ["pick", "place", "dock"],
        liveCapability: null,
        brain: "Scripted",
        spec: "",
      };
    } else if (n.type === "vehicle") {
      const wp = assetsById.get(n.id)?.geometry?.waypoints?.[0] ?? { x: cw / 2, y: ch * 0.75 };
      const c = map(wp);
      actors[n.id] = {
        id: n.id,
        kind: "agv",
        type: n.label?.includes("AGV") ? n.label : "AGV",
        label: n.label ?? n.id,
        position: { x: c.x - AGV_SIZE.w / 2, y: c.y - AGV_SIZE.h / 2 },
        size: AGV_SIZE,
        capabilities: ["transit", "dock", "carry"],
        liveCapability: null,
        brain: "Scheduler · FIFO",
        spec: "",
      };
    }
  }

  if (cfg.intake_zone) {
    const c = map({ x: cfg.intake_zone.x, y: cfg.intake_zone.y });
    zones["feed-I"] = { id: "feed-I", role: "source", label: cfg.intake_zone.label ?? "feed-I", position: c, count: "" };
  }
  if (cfg.output_zone) {
    const c = map({ x: cfg.output_zone.x, y: cfg.output_zone.y });
    zones["sink-O"] = { id: "sink-O", role: "sink", label: cfg.output_zone.label ?? "sink-O", position: c, count: "" };
  }

  return { actors, zones, adjacency };
}
