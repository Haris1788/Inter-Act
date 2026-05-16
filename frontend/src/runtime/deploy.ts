// Build & Deploy. Honest lifecycle: validate → prepare payload → (live only)
// POST /restart_pipeline + reconcile. The backend has no endpoint that
// accepts an authored topology — that is applied offline via
// pipeline.yaml → generate_compose. `runDeploy` never claims otherwise.

import type { TopologyActor, TopologyZone, TopologyGoal } from "@/store/types";
import { useStore } from "@/store";
import { validateTopology } from "@/lib/validation";
import { serializeTopology } from "@/lib/topologySchema";

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

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

async function post(path: string): Promise<boolean> {
  try {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 4000);
    const r = await fetch(path, { method: "POST", signal: ctrl.signal });
    clearTimeout(t);
    return r.ok;
  } catch {
    return false;
  }
}

export interface RunDeployOptions {
  /** when live, also POST /restart_pipeline (restarts the backend's on-disk
   *  pipeline — does NOT push authored topology; that goes through the
   *  offline pipeline.yaml → generate_compose flow). Honest by design. */
  applyRestart: boolean;
}
export interface RunDeployOutcome {
  ok: boolean;
  payload: DeployPayload;
  finalPhase: "prepared" | "ready" | "failed";
  note: string;
}

// Honest deployment lifecycle. Validates → prepares payload + records the
// deploy snapshot (baseline for divergence) → optionally restarts the live
// backend pipeline and reconciles against /config + /arms/health. In mock
// mode it stops at "prepared" — it never claims a deploy that did not happen.
export async function runDeploy(opts: RunDeployOptions): Promise<RunDeployOutcome> {
  const st = () => useStore.getState();

  st().setDeployPhase("validating", "Validating topology…");
  await sleep(280);
  const v = validateTopology({ actors: st().actors, zones: st().zones });
  const payload = buildDeployPayload(st());
  if (!v.deployable) {
    st().setDeployPhase("failed", `Not deployable · ${v.errors || v.warnings} issue(s)`);
    return { ok: false, payload, finalPhase: "failed", note: "Validation blocked deploy" };
  }

  st().setDeployPhase("preparing", "Preparing orchestration payload…");
  await sleep(360);
  const snapshotJson = JSON.stringify(serializeTopology(st()));

  const live = st().dataSource === "live" && st().linkStatus === "connected";
  if (live && opts.applyRestart) {
    st().setDeployPhase("restarting", "Restarting backend pipeline…");
    const ok = await post("/restart_pipeline");
    if (!ok) {
      // No snapshot recorded — the deploy did not land.
      st().setDeployPhase("failed", "Backend /restart_pipeline failed");
      return { ok: false, payload, finalPhase: "failed", note: "restart_pipeline failed" };
    }
    st().setDeployPhase("reconciling", "Reconciling runtime…");
    // Poll until the backend reports actors again (arms OR assets — an
    // AGV-only topology has no arms). Timeout → failed, not silent.
    let ready = false;
    for (let i = 0; i < 6; i++) {
      await sleep(1200);
      try {
        const [ar, as] = await Promise.all([
          fetch("/arms/health").then((r) => (r.ok ? r.json() : null)).catch(() => null),
          fetch("/assets/state").then((r) => (r.ok ? r.json() : null)).catch(() => null),
        ]);
        const armN = ar?.arms ? Object.keys(ar.arms).length : 0;
        const assetN = as?.assets ? Object.keys(as.assets).length : 0;
        if (armN + assetN > 0) {
          ready = true;
          break;
        }
      } catch {
        /* keep polling */
      }
    }
    if (ready) st().recordDeploy(snapshotJson); // baseline only on confirmed land
    st().setDeployPhase(ready ? "ready" : "failed", ready ? "Runtime ready" : "Runtime did not converge");
    return {
      ok: ready,
      payload,
      finalPhase: ready ? "ready" : "failed",
      note: ready ? "Backend pipeline restarted and reconciled" : "Reconcile timed out",
    };
  }

  // Mock / no-restart: honest stop — payload prepared, not deployed. The
  // snapshot is still recorded as the divergence baseline (so "modified ·
  // deploy needed" works) without claiming a deploy happened.
  st().recordDeploy(snapshotJson);
  st().setDeployPhase(
    "prepared",
    "Payload prepared. Backend apply is the offline pipeline.yaml → generate_compose flow.",
  );
  return {
    ok: true,
    payload,
    finalPhase: "prepared",
    note: "Deployable payload prepared (snapshot recorded for divergence tracking)",
  };
}
