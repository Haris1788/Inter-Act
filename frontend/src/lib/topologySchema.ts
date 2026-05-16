// Topology serialization - RF/store graph <-> orchestration JSON.
// Frontend authoring format. NOT the backend YAML compiler (later phase).
// Decentralised model: actors + zones + pool + goal. No handover/transfer
// pads, no deterministic execution routes.

import type {
  TopologyActor,
  TopologyZone,
  TopologyPool,
  TopologyGoal,
} from "@/store/types";

export const TOPOLOGY_SCHEMA_VERSION = 1 as const;

export interface TopologyDoc {
  schemaVersion: number;
  savedAt: string;
  actors: TopologyActor[];
  zones: TopologyZone[];
  pool: TopologyPool;
  goal: TopologyGoal;
}

type TopologyState = {
  actors: Record<string, TopologyActor>;
  zones: Record<string, TopologyZone>;
  pool: TopologyPool;
  goal: TopologyGoal;
};

export function serializeTopology(s: TopologyState): TopologyDoc {
  return {
    schemaVersion: TOPOLOGY_SCHEMA_VERSION,
    savedAt: new Date().toISOString(),
    actors: Object.values(s.actors),
    zones: Object.values(s.zones),
    pool: s.pool,
    goal: s.goal,
  };
}

export function docToState(doc: TopologyDoc): TopologyState {
  const actors: Record<string, TopologyActor> = {};
  for (const a of doc.actors) actors[a.id] = a;
  const zones: Record<string, TopologyZone> = {};
  for (const z of doc.zones) zones[z.id] = z;
  return { actors, zones, pool: doc.pool, goal: doc.goal };
}

export type ParseResult =
  | { ok: true; doc: TopologyDoc }
  | { ok: false; error: string };

export function parseTopology(raw: string): ParseResult {
  let json: unknown;
  try {
    json = JSON.parse(raw);
  } catch {
    return { ok: false, error: "Invalid JSON" };
  }
  const d = json as Partial<TopologyDoc>;
  if (typeof d !== "object" || d === null)
    return { ok: false, error: "Not an object" };
  if (typeof d.schemaVersion !== "number")
    return { ok: false, error: "Missing schemaVersion" };
  if (d.schemaVersion > TOPOLOGY_SCHEMA_VERSION)
    return { ok: false, error: `Unsupported schemaVersion ${d.schemaVersion}` };
  if (!Array.isArray(d.actors)) return { ok: false, error: "Missing actors[]" };
  // Tolerate older/partial docs: backfill optional sections.
  const doc: TopologyDoc = {
    schemaVersion: d.schemaVersion,
    savedAt:
      typeof d.savedAt === "string" ? d.savedAt : new Date().toISOString(),
    actors: d.actors as TopologyActor[],
    zones: Array.isArray(d.zones) ? (d.zones as TopologyZone[]) : [],
    pool: (d.pool as TopologyPool) ?? {
      id: "pool",
      bounds: { x: 350, y: 410, w: 620, h: 200 },
      label: "Mobile Transport Pool",
      sub: "",
    },
    goal: (d.goal as TopologyGoal) ?? { text: "", constraints: {} },
  };
  return { ok: true, doc };
}
