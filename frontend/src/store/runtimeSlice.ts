import type { StateCreator } from "zustand";
import type { AgvPath, AgvRuntimePosition, ReplayPosition, RootStore, RuntimeSlice } from "./types";

const BUS_CAP = 40;
const LOG_CAP = 200;
const EVENT_CAP = 300; // bounded - long-session memory safety

// Exported so schema-driven surfaces (Inspector) can render consistent
// controls for any actor that has no live runtime entry yet (e.g. just
// drag-created, not yet seen by a driver). No preset/origin special-case.
export const DEFAULT_RUNTIME = {
  status: "idle" as const,
  phase: "",
  uptime: "",
  metrics: { cpu: 0, memMb: 0, battery: 100, busLatencyMs: 0 },
};

export const createRuntimeSlice: StateCreator<
  RootStore,
  [],
  [],
  RuntimeSlice
> = (set) => ({
  actorRuntime: {},
  busMessages: [],
  logLines: [],
  validation: [],
  dataSource: "mock",
  linkStatus: "connecting",
  events: [],
  // Phase 5 Stage 8 Track C — replay scrub. Off by default.
  replayMode: false,
  replayPositions: {},
  // Phase 5.1 Stage 4 — planned AGV paths from agv_path SocketIO event.
  agvPaths: {},

  pushBusMessage: (m) =>
    set((s) => ({ busMessages: [...s.busMessages, m].slice(-BUS_CAP) })),

  pushLogLine: (l) =>
    set((s) => ({ logLines: [...s.logLines, l].slice(-LOG_CAP) })),

  pushEvent: (e) =>
    set((s) => {
      // Uniform HH:MM:SS - accept a caller ts only if it already matches,
      // otherwise derive locally (keeps the timeline column aligned).
      const ts =
        e.ts && /^\d{2}:\d{2}:\d{2}$/.test(e.ts)
          ? e.ts
          : new Date().toISOString().slice(11, 19);
      return {
        events: [
          ...s.events,
          {
            ...e,
            id: `ev-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
            ts,
          },
        ].slice(-EVENT_CAP),
      };
    }),

  setActorRuntime: (id, patch) =>
    set((s) => ({
      actorRuntime: {
        ...s.actorRuntime,
        [id]: { ...DEFAULT_RUNTIME, ...s.actorRuntime[id], ...patch },
      },
    })),

  // Stage 5 Track B — observational runtime position from `agv_position` event.
  // Stored separately from authored position; never sent back to backend.
  setRuntimePosition: (id: string, pos: AgvRuntimePosition) =>
    set((s) => ({
      actorRuntime: {
        ...s.actorRuntime,
        [id]: {
          ...DEFAULT_RUNTIME,
          ...s.actorRuntime[id],
          runtimePosition: pos,
        },
      },
    })),

  setConnection: (source, status) =>
    set(() => ({ dataSource: source, linkStatus: status })),

  // Phase 5 Stage 8 Track C — enter replay mode with a positions snapshot.
  setReplayPositions: (positions: Record<string, ReplayPosition>) =>
    set(() => ({ replayMode: true, replayPositions: positions })),

  // Phase 5 Stage 8 Track C — exit replay mode, clear historical positions.
  clearReplayMode: () =>
    set(() => ({ replayMode: false, replayPositions: {} })),

  // Phase 5.1 Stage 4 — AGV path preview actions.
  setAgvPath: (actorId: string, path: AgvPath) =>
    set((s) => {
      const existing = s.agvPaths[actorId];
      // Idempotent: ignore if the incoming epoch is older than what we have.
      if (existing && path.epoch < existing.epoch) return s;
      return { agvPaths: { ...s.agvPaths, [actorId]: path } };
    }),

  clearAgvPath: (actorId: string) =>
    set((s) => {
      if (!(actorId in s.agvPaths)) return s;
      const next = { ...s.agvPaths };
      delete next[actorId];
      return { agvPaths: next };
    }),

  hydrateRuntime: (patch) => set(() => ({ ...patch })),
});
