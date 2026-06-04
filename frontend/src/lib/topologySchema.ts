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
import { normalizeActor } from "./actorNormalizer";

export const TOPOLOGY_SCHEMA_VERSION = 1 as const;

export interface TopologyDoc {
  schemaVersion: number;
  savedAt: string;
  actors: TopologyActor[];
  /** Phase 7 Stage F: LEGACY DOC FIELD (compat). Written for old readers +
   *  round-trip; on import it is folded into `actors` (kind:"zone"). The
   *  runtime has no zones slice — zone actors are the source of truth. */
  zones: TopologyZone[];
  pool: TopologyPool;
  goal: TopologyGoal;
}

type TopologyState = {
  actors: Record<string, TopologyActor>;
  pool: TopologyPool;
  goal: TopologyGoal;
};

export function serializeTopology(s: TopologyState): TopologyDoc {
  const actorList = Object.values(s.actors);
  return {
    schemaVersion: TOPOLOGY_SCHEMA_VERSION,
    savedAt: new Date().toISOString(),
    actors: actorList,
    // Phase 7 Stage B: zones live in `actors` (kind:"zone"). Still emit a
    // derived `zones[]` so the doc shape + older readers + round-trip hold.
    zones: actorList
      .filter((a) => a.kind === "zone")
      .map((a) => ({
        id: a.id,
        role: a.subtype === "sink" ? ("sink" as const) : ("source" as const),
        label: a.label,
        position: a.position,
        count: "",
      })),
    pool: s.pool,
    goal: s.goal,
  };
}

export function docToState(doc: TopologyDoc): TopologyState {
  // Import / restore is a creation path → same canonical normalizer.
  const actors: Record<string, TopologyActor> = {};
  for (const a of doc.actors) actors[a.id] = normalizeActor(a);
  // Phase 7 Stage F: legacy `zones[]` (old workspaces) → kind:"zone" actors.
  // The runtime has no zones slice; `actors` is the single source of truth. A
  // zone already present in doc.actors (new saves) wins over the legacy entry.
  for (const z of doc.zones ?? []) {
    if (actors[z.id]) continue;
    actors[z.id] = normalizeActor({
      id: z.id,
      kind: "zone",
      subtype: z.role,
      label: z.label,
      position: z.position,
    });
  }
  return { actors, pool: doc.pool, goal: doc.goal };
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
