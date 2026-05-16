// Build & Deploy seam. Phase 4 prepares the frontend→backend handoff
// CONTRACT only — it does NOT call the backend or rewrite the orchestration
// compiler. A later phase wires `deployTopology` to a real endpoint.

import type { TopologyActor, TopologyZone, TopologyGoal } from "@/store/types";

export const DEPLOY_PAYLOAD_VERSION = 1 as const;

export interface DeployActor {
  id: string;
  kind: "arm" | "agv";
  type: string;
  label: string;
  capabilities: string[];
  brain: string;
  position: { x: number; y: number };
}

export interface DeployPayload {
  payloadVersion: number;
  generatedAt: string;
  actors: DeployActor[];
  zones: { id: string; role: string; label: string }[];
  goal: TopologyGoal;
}

type DeployState = {
  actors: Record<string, TopologyActor>;
  zones: Record<string, TopologyZone>;
  goal: TopologyGoal;
};

export function buildDeployPayload(s: DeployState): DeployPayload {
  return {
    payloadVersion: DEPLOY_PAYLOAD_VERSION,
    generatedAt: new Date().toISOString(),
    actors: Object.values(s.actors).map((a) => ({
      id: a.id,
      kind: a.kind,
      type: a.type,
      label: a.label,
      capabilities: a.capabilities,
      brain: a.brain,
      position: a.position,
    })),
    zones: Object.values(s.zones).map((z) => ({ id: z.id, role: z.role, label: z.label })),
    goal: s.goal,
  };
}

export interface DeployResult {
  ok: boolean;
  payload: DeployPayload;
  note: string;
}

// Seam stub. No network. Returns the payload so the UI can preview/confirm
// the deployable topology; a future phase replaces the body with a real
// POST to the backend orchestration compiler.
export function deployTopology(payload: DeployPayload): DeployResult {
  return {
    ok: true,
    payload,
    note: `Prepared ${payload.actors.length} actors · ${payload.zones.length} zones (seam — backend deploy wired in a later phase)`,
  };
}
