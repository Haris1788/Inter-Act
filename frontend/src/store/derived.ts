// Derived, cross-slice read-only views (Phase 5 coherence/lifecycle).
// Composing reads across slices in a hook is fine - the strict separation is
// about state OWNERSHIP/writes, not derived selectors.
import { useMemo } from "react";
import { useStore } from "./index";
import { validateTopology, type ValidationReport } from "@/lib/validation";
import { computeCoherence, type CoherenceResult } from "@/lib/coherence";
import { deriveLifecycle, type LifecycleView } from "@/lib/lifecycle";
import { parseTopology, type TopologyDoc } from "@/lib/topologySchema";

import type { OperationalEvent } from "./types";

/** Per-actor operational history (observability). Memoized. */
export function useActorEvents(
  actorId: string | null,
  limit = 40,
): OperationalEvent[] {
  const events = useStore((s) => s.events);
  return useMemo(() => {
    if (!actorId) return [];
    return events.filter((e) => e.actorId === actorId).slice(-limit);
  }, [events, actorId, limit]);
}

export function useTopologyValidation(): ValidationReport {
  const actors = useStore((s) => s.actors);
  // Phase 7 Stage F: zones are kind:"zone" actors; validation reads them from actors.
  return useMemo(() => validateTopology({ actors }), [actors]);
}

function useDeployedDoc(): TopologyDoc | null {
  const json = useStore((s) => s.deployedJson);
  return useMemo(() => {
    if (!json) return null;
    const r = parseTopology(json);
    return r.ok ? r.doc : null;
  }, [json]);
}

export function useCoherence(): CoherenceResult {
  const actors = useStore((s) => s.actors);
  // Coherence only needs the SET of runtime ids, not their telemetry. Subscribe
  // to a stable string key so 60fps runtime patches don't re-render every node.
  const runtimeKey = useStore((s) =>
    Object.keys(s.actorRuntime).sort().join(","),
  );
  const dataSource = useStore((s) => s.dataSource);
  const deployed = useDeployedDoc();
  return useMemo(
    () =>
      computeCoherence(
        actors,
        new Set(runtimeKey ? runtimeKey.split(",") : []),
        deployed,
        dataSource === "live",
      ),
    [actors, runtimeKey, deployed, dataSource],
  );
}

export function useLifecycle(): LifecycleView {
  const validation = useTopologyValidation();
  const coherence = useCoherence();
  const actorCount = useStore((s) => Object.keys(s.actors).length);
  // stable boolean - avoids Topbar re-render on every metric tick
  const anyDown = useStore((s) =>
    Object.values(s.actorRuntime).some((r) => r.status === "down"),
  );
  const deployedJson = useStore((s) => s.deployedJson);
  const dataSource = useStore((s) => s.dataSource);
  const linkStatus = useStore((s) => s.linkStatus);
  const deployPhase = useStore((s) => s.deployPhase);

  return useMemo(() => {
    const everDeployed = deployedJson != null;
    // "deploy needed" is divergence from the deploy snapshot - NOT the
    // autosave dirty flag (which is about local persistence, not deployment).
    const dirtySinceDeploy =
      everDeployed &&
      (coherence.summary.modified > 0 || coherence.summary.draft > 0);
    return deriveLifecycle({
      deployable: validation.deployable,
      hasActors: actorCount > 0,
      dirtySinceDeploy,
      everDeployed,
      dataSource,
      linkStatus,
      anyDown,
      coherence,
      deployPhase,
    });
  }, [
    validation,
    coherence,
    actorCount,
    anyDown,
    deployedJson,
    dataSource,
    linkStatus,
    deployPhase,
  ]);
}
