import { useStore, useStoreEq } from "./index";
import { DEFAULT_RUNTIME } from "./runtimeSlice";
import type { ActorId, TopologyActor, ActorRuntime } from "./types";

// Slice-scoped hooks. Components subscribe to the narrowest slice they need so
// a spatial tick never re-renders topology/chrome.
export const useActors = () => useStore((s) => s.actors);
export const useActor = (id: ActorId) => useStore((s) => s.actors[id]);
export const useZones = () => useStore((s) => s.zones);
export const useGoal = () => useStore((s) => s.goal);
export const useSelectedActorId = () => useStore((s) => s.selectedActorId);

export const useActorRuntime = (id: ActorId) => useStore((s) => s.actorRuntime[id]);

// Phase 14 - ONE authoritative subscription for the Inspector. Header, body,
// runtime sections and actions all read from this single bundle so they can
// never tear (stale header while body is fresh). Runtime falls back to the
// canonical default so schema-driven rendering still holds for actors with
// no live runtime yet (Phase 13 invariant preserved).
export interface SelectedActorBundle {
  selectedId: ActorId | null;
  actor: TopologyActor | undefined;
  runtime: ActorRuntime;
}
export const useSelectedActorBundle = (): SelectedActorBundle =>
  useStoreEq(
    (s): SelectedActorBundle => {
      const selectedId = s.selectedActorId;
      const actor = selectedId ? s.actors[selectedId] : undefined;
      const runtime =
        (selectedId && s.actorRuntime[selectedId]) || DEFAULT_RUNTIME;
      return { selectedId, actor, runtime };
    },
    (a, b) =>
      a.selectedId === b.selectedId &&
      a.actor === b.actor &&
      a.runtime === b.runtime,
  );

export const useLogsCollapsed = () => useStore((s) => s.logsCollapsed);
export const useLogsHeight = () => useStore((s) => s.logsHeight);
// ── Editor / authoring (Phase 4) ──
export const useEditorMode = () => useStore((s) => s.mode);
export const useSelectedIds = () => useStore((s) => s.selectedIds);
export const useContextMenu = () => useStore((s) => s.contextMenu);
export const useDirty = () => useStore((s) => s.dirty);
export const useDeployPhase = () => useStore((s) => s.deployPhase);
export const useDeployMessage = () => useStore((s) => s.deployMessage);
export const useDeployedAt = () => useStore((s) => s.deployedAt);
export const useFocusRequest = () => useStore((s) => s.focusRequest);
export const useEvents = () => useStore((s) => s.events);

export const useDataSource = () => useStore((s) => s.dataSource);
export const useLinkStatus = () => useStore((s) => s.linkStatus);
export const useBusMessages = () => useStore((s) => s.busMessages);
export const useLogLines = () => useStore((s) => s.logLines);
export const useValidation = () => useStore((s) => s.validation);

export const useTrajectories = () => useStore((s) => s.trajectories);
export const useReachability = () => useStore((s) => s.reachability);
export const useDockGlows = () => useStore((s) => s.dockGlows);
export const useAgvKinematics = () => useStore((s) => s.agvKinematics);
export const useArmAngles = () => useStore((s) => s.armAngles);
export const useTablePkgs = () => useStore((s) => s.tablePkgs);
export const useAgvCargo = () => useStore((s) => s.agvCargo);
/** Phase-6 payload entities. */
export const usePayloads = () => useStore((s) => s.payloads);
/** Stage 5 Track B — all actor runtime entries (for runtimePosition). */
export const useActorRuntimeAll = () => useStore((s) => s.actorRuntime);
/** Phase 5 Stage 8 Track C — replay scrub flag. */
export const useReplayMode = () => useStore((s) => s.replayMode);
/** Phase 5 Stage 8 Track C — historical AGV positions at the scrub cursor. */
export const useReplayPositions = () => useStore((s) => s.replayPositions);
/** Phase 5.1 Stage 4 — planned AGV paths from agv_path SocketIO event. */
export const useAgvPaths = () => useStore((s) => s.agvPaths);
