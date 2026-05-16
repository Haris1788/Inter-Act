import { useStore } from "./index";
import type { ActorId } from "./types";

// Slice-scoped hooks. Components subscribe to the narrowest slice they need so
// a spatial tick never re-renders topology/chrome.
export const useActors = () => useStore((s) => s.actors);
export const useActor = (id: ActorId) => useStore((s) => s.actors[id]);
export const useZones = () => useStore((s) => s.zones);
export const usePool = () => useStore((s) => s.pool);
export const useGoal = () => useStore((s) => s.goal);
export const useSelectedActorId = () => useStore((s) => s.selectedActorId);

export const useActorRuntime = (id: ActorId) => useStore((s) => s.actorRuntime[id]);
export const useDataSource = () => useStore((s) => s.dataSource);
export const useLinkStatus = () => useStore((s) => s.linkStatus);
export const useBusMessages = () => useStore((s) => s.busMessages);
export const useBusRate = () => useStore((s) => s.busRate);
export const useLogLines = () => useStore((s) => s.logLines);
export const useValidation = () => useStore((s) => s.validation);

export const useTrajectories = () => useStore((s) => s.trajectories);
export const useReachability = () => useStore((s) => s.reachability);
export const useDockGlows = () => useStore((s) => s.dockGlows);
export const useAgvKinematics = () => useStore((s) => s.agvKinematics);
