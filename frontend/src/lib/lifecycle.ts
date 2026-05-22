// Topology lifecycle status derivation. Subtle + operational, not flashy.
// Honest: "deployed/running" only when the backend is actually live; in mock
// mode the furthest state is "prepared" (payload ready, backend deploy offline).
import type { DataSource, LinkStatus, DeployPhase } from "@/store/types";
import type { CoherenceResult } from "./coherence";

export type { DeployPhase };

export type LifecycleStatus =
  | "draft"
  | "modified"
  | "validated"
  | "deployable"
  | "deploying"
  | "prepared"
  | "running"
  | "degraded"
  | "disconnected"
  | "stale";

export type Tone = "ok" | "warn" | "idle" | "info" | "err";

export interface LifecycleView {
  status: LifecycleStatus;
  tone: Tone;
  label: string;
}

interface Input {
  deployable: boolean;
  hasActors: boolean;
  dirtySinceDeploy: boolean;
  everDeployed: boolean;
  dataSource: DataSource;
  linkStatus: LinkStatus;
  anyDown: boolean;
  coherence: CoherenceResult;
  deployPhase: DeployPhase;
}

export function deriveLifecycle(i: Input): LifecycleView {
  const live = i.dataSource === "live";

  // active deploy phases win
  if (i.deployPhase === "validating") return { status: "deploying", tone: "info", label: "validating" };
  if (i.deployPhase === "preparing") return { status: "deploying", tone: "info", label: "preparing" };
  if (i.deployPhase === "restarting") return { status: "deploying", tone: "info", label: "restarting runtime" };
  if (i.deployPhase === "reconciling") return { status: "deploying", tone: "info", label: "reconciling" };
  if (i.deployPhase === "failed") return { status: "draft", tone: "err", label: "deploy failed" };

  if (!i.hasActors) return { status: "draft", tone: "idle", label: "empty" };
  if (!i.deployable) return { status: "draft", tone: "warn", label: "draft · not deployable" };

  if (live && i.linkStatus === "disconnected")
    return { status: "disconnected", tone: "err", label: "runtime disconnected" };

  if (live && i.coherence.summary.stale > 0)
    return { status: "stale", tone: "warn", label: `${i.coherence.summary.stale} actor(s) not running` };

  if (live && i.everDeployed && i.anyDown)
    return { status: "degraded", tone: "warn", label: "runtime degraded" };

  if (i.everDeployed && i.dirtySinceDeploy)
    return { status: "modified", tone: "warn", label: "modified · deploy needed" };

  if (live && i.everDeployed)
    return { status: "running", tone: "ok", label: "running" };

  if (i.deployPhase === "prepared" || (i.everDeployed && !live))
    return { status: "prepared", tone: "info", label: "payload prepared" };

  return { status: "deployable", tone: "ok", label: "deployable" };
}
