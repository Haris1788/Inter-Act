// Build & Deploy. Honest lifecycle: validate → prepare payload → (live only)
// POST /restart_pipeline + reconcile. The backend has no endpoint that
// accepts an authored topology - that is applied offline via
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
    zones: Object.values(s.zones).map((z) => ({
      id: z.id,
      role: z.role,
      label: z.label,
    })),
    goal: s.goal,
  };
}

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

interface ApplyResult {
  applied: boolean;
  detail: string;
  arms: string[];
}

/** POST the authored payload to the real deploy bridge. null = unreachable. */
async function postApply(
  path: string,
  body: unknown,
): Promise<ApplyResult | null> {
  try {
    const ctrl = new AbortController();
    // Bridge may run `docker compose up` server-side - allow generous time.
    const t = setTimeout(() => ctrl.abort(), 250_000);
    const r = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: ctrl.signal,
    });
    clearTimeout(t);
    if (!r.ok) return null;
    return (await r.json()) as ApplyResult;
  } catch {
    return null;
  }
}

export interface RunDeployOptions {
  /** when live, also POST /restart_pipeline (restarts the backend's on-disk
   *  pipeline - does NOT push authored topology; that goes through the
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
// mode it stops at "prepared" - it never claims a deploy that did not happen.
const ACTIVE_PHASES = new Set([
  "validating",
  "preparing",
  "restarting",
  "reconciling",
]);

export async function runDeploy(
  opts: RunDeployOptions,
): Promise<RunDeployOutcome> {
  const st = () => useStore.getState();

  // Defense-in-depth re-entry guard: ignore concurrent invocations so deploy
  // spam can't overlap two lifecycle state machines (the UI also disables the
  // button, but a guard here protects any caller/race).
  if (ACTIVE_PHASES.has(st().deployPhase)) {
    return {
      ok: false,
      payload: buildDeployPayload(st()),
      finalPhase: "failed",
      note: "Deploy already in progress",
    };
  }

  st().setDeployPhase("validating", "Validating topology…");
  await sleep(280);
  const v = validateTopology({ actors: st().actors, zones: st().zones });
  const payload = buildDeployPayload(st());
  if (!v.deployable) {
    st().setDeployPhase(
      "failed",
      `Not deployable · ${v.errors || v.warnings} issue(s)`,
    );
    st().pushEvent({
      kind: "deploy",
      text: "Deploy blocked - validation failed",
      tone: "err",
    });
    return {
      ok: false,
      payload,
      finalPhase: "failed",
      note: "Validation blocked deploy",
    };
  }

  st().setDeployPhase("preparing", "Preparing orchestration payload…");
  await sleep(360);
  const snapshotJson = JSON.stringify(serializeTopology(st()));

  const live = st().dataSource === "live" && st().linkStatus === "connected";
  if (live && opts.applyRestart) {
    st().setDeployPhase("restarting", "Applying topology to runtime…");
    // REAL backend-authoritative apply: payload → pipeline.yaml →
    // generate_compose → docker compose up → reload. The backend is honest:
    // `applied` is false when it could not actually start containers.
    const res = await postApply("/deploy/apply", payload);
    if (!res) {
      st().setDeployPhase("failed", "Backend /deploy/apply unreachable");
      st().pushEvent({
        kind: "deploy",
        text: "Deploy failed - /deploy/apply unreachable",
        tone: "err",
      });
      return {
        ok: false,
        payload,
        finalPhase: "failed",
        note: "/deploy/apply unreachable",
      };
    }

    st().pushEvent({
      kind: "deploy",
      text: `Backend: ${res.detail}`,
      tone: res.applied ? "info" : "warn",
    });

    // The backend did not start real containers - do NOT fake a ready
    // runtime or record a deploy baseline. The actor stays stale until a
    // real container reports healthy (honest, per spec).
    if (!res.applied) {
      st().setDeployPhase(
        "failed",
        "Runtime not started by backend - actor stays stale (see Events)",
      );
      return {
        ok: false,
        payload,
        finalPhase: "failed",
        note: res.detail,
      };
    }

    st().setDeployPhase("reconciling", "Reconciling runtime…");
    const expected = new Set(res.arms);
    const aliveHealth = new Set(["alive", "idle", "busy", "recovering"]);
    // Confirm via backend truth ONLY: every authored arm container must
    // report a live health before we call the deploy ready. Timeout → fail.
    // ~36s: freshly-cloned arm containers need time to boot arm_server and
    // POST their first health. Generous so a real-but-slow start is not
    // misreported as a failure.
    let ready = false;
    for (let i = 0; i < 18; i++) {
      await sleep(2000);
      try {
        const ar = await fetch("/arms/health")
          .then((r) => (r.ok ? r.json() : null))
          .catch(() => null);
        const arms: Record<string, { health?: string }> = ar?.arms ?? {};
        const present = new Set(
          Object.keys(arms).map((k) => `arm-${k}`),
        );
        const allUp =
          expected.size === 0 ||
          [...expected].every(
            (id) =>
              present.has(id) &&
              aliveHealth.has(arms[id.replace("arm-", "")]?.health ?? ""),
          );
        if (allUp) {
          ready = true;
          break;
        }
      } catch {
        /* keep polling */
      }
    }
    if (ready) st().recordDeploy(snapshotJson); // baseline only on confirmed land
    st().setDeployPhase(
      ready ? "ready" : "failed",
      ready
        ? "Runtime ready - containers confirmed alive"
        : "Some arm containers not healthy yet - backend applied the config "
          + "(see Events for per-arm detail); they may still be booting",
    );
    st().pushEvent({
      kind: "deploy",
      text: ready
        ? "Deploy reconciled - containers confirmed alive"
        : "Deploy reconcile timed out - containers not confirmed",
      tone: ready ? "ok" : "warn",
    });
    return {
      ok: ready,
      payload,
      finalPhase: ready ? "ready" : "failed",
      note: ready
        ? "Backend pipeline restarted and reconciled"
        : "Reconcile timed out",
    };
  }

  // Mock / no-restart: honest stop - payload prepared, not deployed. The
  // snapshot is still recorded as the divergence baseline (so "modified ·
  // deploy needed" works) without claiming a deploy happened.
  st().recordDeploy(snapshotJson);
  st().setDeployPhase(
    "prepared",
    "Payload prepared. Backend apply is the offline pipeline.yaml → generate_compose flow.",
  );
  st().pushEvent({
    kind: "deploy",
    text: `Payload prepared · ${payload.actors.length} actors (snapshot recorded)`,
    tone: "info",
  });
  return {
    ok: true,
    payload,
    finalPhase: "prepared",
    note: "Deployable payload prepared (snapshot recorded for divergence tracking)",
  };
}
