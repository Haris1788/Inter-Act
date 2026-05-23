// Topology serialization - RF/store graph <-> orchestration JSON.
// Frontend authoring format. NOT the backend YAML compiler (later phase).
// Decentralised model: actors + zones + goal. No pool, no handover/transfer
// pads, no deterministic execution routes.
// Phase 5.1 bug-fix: pool removed from TopologyDoc (dashboard widget deleted).
// Old saved docs with a pool field are tolerated on load (field silently ignored).

import type {
  TopologyActor,
  TopologyZone,
  TopologyGoal,
} from "@/store/types";
import { normalizeActor } from "./actorNormalizer";

export const TOPOLOGY_SCHEMA_VERSION = 1 as const;

export interface TopologyDoc {
  schemaVersion: number;
  savedAt: string;
  actors: TopologyActor[];
  zones: TopologyZone[];
  goal: TopologyGoal;
}

type TopologyState = {
  actors: Record<string, TopologyActor>;
  zones: Record<string, TopologyZone>;
  goal: TopologyGoal;
};

export function serializeTopology(s: TopologyState): TopologyDoc {
  return {
    schemaVersion: TOPOLOGY_SCHEMA_VERSION,
    savedAt: new Date().toISOString(),
    actors: Object.values(s.actors),
    zones: Object.values(s.zones),
    goal: s.goal,
  };
}

export function docToState(doc: TopologyDoc): TopologyState {
  // Import / restore is a creation path → same canonical normalizer.
  const actors: Record<string, TopologyActor> = {};
  for (const a of doc.actors) actors[a.id] = normalizeActor(a);
  const zones: Record<string, TopologyZone> = {};
  for (const z of doc.zones) zones[z.id] = z;
  return { actors, zones, goal: doc.goal };
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
  const d = json as Partial<TopologyDoc & { pool?: unknown }>;
  if (typeof d !== "object" || d === null)
    return { ok: false, error: "Not an object" };
  if (typeof d.schemaVersion !== "number")
    return { ok: false, error: "Missing schemaVersion" };
  if (d.schemaVersion > TOPOLOGY_SCHEMA_VERSION)
    return { ok: false, error: `Unsupported schemaVersion ${d.schemaVersion}` };
  if (!Array.isArray(d.actors)) return { ok: false, error: "Missing actors[]" };
  // Tolerate older/partial docs: backfill optional sections.
  // pool is intentionally ignored (Phase 5.1: pool widget deleted).
  const doc: TopologyDoc = {
    schemaVersion: d.schemaVersion,
    savedAt:
      typeof d.savedAt === "string" ? d.savedAt : new Date().toISOString(),
    actors: d.actors as TopologyActor[],
    zones: Array.isArray(d.zones) ? (d.zones as TopologyZone[]) : [],
    goal: (d.goal as TopologyGoal) ?? { text: "", constraints: {} },
  };
  return { ok: true, doc };
}
