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
  running?: boolean;
}

interface DeployStatus {
  phase: string; // idle | materializing | done
  running: boolean;
  applied: boolean;
  detail: string;
  arms: string[];
}

/** POST the authored payload. The bridge returns INSTANTLY (heavy work is
 *  backgrounded), so a short timeout is correct. null = unreachable. */
async function postApply(
  path: string,
  body: unknown,
): Promise<ApplyResult | null> {
  try {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 15_000);
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

async function getDeployStatus(): Promise<DeployStatus | null> {
  try {
    const r = await fetch("/deploy/status");
    return r.ok ? ((await r.json()) as DeployStatus) : null;
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
      tone: "info",
    });

    // Honest immediate stop: backend could not even start (read-only
    // pipeline.yaml / docker unavailable). It is not running anything.
    if (!res.running && !res.applied) {
      st().setDeployPhase(
        "failed",
        "Runtime not started by backend - actor stays stale (see Events)",
      );
      return { ok: false, payload, finalPhase: "failed", note: res.detail };
    }

    // Phase A: poll the background materialize to completion. The request
    // already returned, so this is cheap polling - it can never make the
    // deploy call itself time out, for any actor count.
    st().setDeployPhase("restarting", "Building images / starting containers…");
    let status: DeployStatus | null = null;
    for (let i = 0; i < 150; i++) {
      // ~5 min ceiling: first-ever shared-image build is slow.
      await sleep(2000);
      status = await getDeployStatus();
      if (status) {
        st().setDeployPhase("restarting", status.detail || "Materializing…");
        if (!status.running) break;
      }
    }
    if (status && !status.applied) {
      st().setDeployPhase("failed", status.detail || "Backend did not apply");
      st().pushEvent({
        kind: "deploy",
        text: `Backend: ${status.detail}`,
        tone: "warn",
      });
      return {
        ok: false,
        payload,
        finalPhase: "failed",
        note: status.detail || "not applied",
      };
    }

    // Phase B: confirm via backend truth - every authored arm container
    // must report a live health before the deploy is "ready".
    st().setDeployPhase("reconciling", "Reconciling runtime…");
    const expected = new Set(res.arms);
    const aliveHealth = new Set(["alive", "idle", "busy", "recovering"]);
    let ready = false;
    for (let i = 0; i < 30; i++) {
      await sleep(2000);
      try {
        const ar = await fetch("/arms/health")
          .then((r) => (r.ok ? r.json() : null))
          .catch(() => null);
        const arms: Record<string, { health?: string }> = ar?.arms ?? {};
        const present = new Set(Object.keys(arms).map((k) => `arm-${k}`));
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
        : "Backend applied the config; some containers still booting "
          + "(see Events for per-actor detail)",
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
      note: ready ? "Applied and reconciled" : "Reconcile timed out",
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
