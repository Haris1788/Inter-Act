// Authored ↔ deployed ↔ runtime coherence. Two truths (prompt): the editable
// authored topology and the backend-authoritative runtime. This computes,
// per actor, where it stands between them. Pure; read-only over slices.
import type { TopologyActor } from "@/store/types";
import type { TopologyDoc } from "./topologySchema";

export type Coherence = "draft" | "modified" | "deployed" | "stale";

export interface CoherenceResult {
  byId: Record<string, Coherence>;
  /** actors live in the runtime registry but not in the authored topology */
  runtimeOnly: string[];
  summary: {
    draft: number;
    modified: number;
    deployed: number;
    stale: number;
    runtimeOnly: number;
  };
}

function actorFingerprint(a: TopologyActor): string {
  return JSON.stringify([
    a.id,
    a.kind,
    a.type,
    a.label,
    a.capabilities,
    a.position,
  ]);
}

export function computeCoherence(
  authored: Record<string, TopologyActor>,
  runtimeIds: Set<string>,
  deployed: TopologyDoc | null,
  live: boolean,
): CoherenceResult {
  const byId: Record<string, Coherence> = {};
  const deployedById = new Map<string, TopologyActor>();
  if (deployed) for (const a of deployed.actors) deployedById.set(a.id, a);

  for (const a of Object.values(authored)) {
    if (!deployed || !deployedById.has(a.id)) {
      byId[a.id] = "draft";
      continue;
    }
    const prev = deployedById.get(a.id)!;
    if (actorFingerprint(prev) !== actorFingerprint(a)) {
      byId[a.id] = "modified";
      continue;
    }
    // deployed & unchanged - but is it actually running?
    byId[a.id] = live && !runtimeIds.has(a.id) ? "stale" : "deployed";
  }

  const runtimeOnly = [...runtimeIds].filter((id) => !authored[id]);

  const summary = {
    draft: 0,
    modified: 0,
    deployed: 0,
    stale: 0,
    runtimeOnly: runtimeOnly.length,
  };
  for (const c of Object.values(byId)) summary[c]++;

  return { byId, runtimeOnly, summary };
}
