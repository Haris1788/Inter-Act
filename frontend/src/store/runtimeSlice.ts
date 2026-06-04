import type { StateCreator } from "zustand";
import type { ActiveAi, AgvRoute, RootStore, RuntimeSlice } from "./types";

// US 34.1: caps raised so the live coordination view is useful for longer
// sessions. Bus panel previously stopped scrolling at 40 messages; log panel
// at 200. Operator complaint: "messages disappear before we read them" and
// "bus doesn't show all messages the backend bus emits".
const BUS_CAP = 300;
const LOG_CAP = 300;
const EVENT_CAP = 300; // bounded - long-session memory safety

// Default active-AI badge. Replaced as bus events arrive (intent → tactical,
// task_announce → planner, etc.) so the left-foot label always reflects what
// the system is doing RIGHT NOW.
const DEFAULT_ACTIVE_AI: ActiveAi = {
  model: "qwen2.5:0.5b",
  role: "idle",
  source: "system",
  tsMs: 0,
};

// Exported so schema-driven surfaces (Inspector) can render consistent
// controls for any actor that has no live runtime entry yet (e.g. just
// drag-created, not yet seen by a driver). No preset/origin special-case.
export const DEFAULT_RUNTIME = {
  status: "idle" as const,
  phase: "",
  bid: null,
  winning: false,
  uptime: "",
  metrics: { cpu: 0, memMb: 0, battery: 100, busLatencyMs: 0 },
  taskQueue: [],
};

export const createRuntimeSlice: StateCreator<
  RootStore,
  [],
  [],
  RuntimeSlice
> = (set) => ({
  actorRuntime: {},
  busMessages: [],
  busRate: 0,
  logLines: [],
  validation: [],
  contracts: [],
  dataSource: "mock",
  linkStatus: "connecting",
  events: [],
  agvRoutes: {},
  activeAi: DEFAULT_ACTIVE_AI,

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

  setConnection: (source, status) =>
    set(() => ({ dataSource: source, linkStatus: status })),

  setAgvRoute: (route: AgvRoute) =>
    set((s) => ({ agvRoutes: { ...s.agvRoutes, [route.agvId]: route } })),

  clearAgvRoute: (agvId: string) =>
    set((s) => {
      if (!s.agvRoutes[agvId]) return {};
      const next = { ...s.agvRoutes };
      delete next[agvId];
      return { agvRoutes: next };
    }),

  setActiveAi: (patch) =>
    set((s) => ({ activeAi: { ...s.activeAi, ...patch } })),

  hydrateRuntime: (patch) => set(() => ({ ...patch })),
});
