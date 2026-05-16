import type { StateCreator } from "zustand";
import type { RootStore, RuntimeSlice } from "./types";

const BUS_CAP = 40;
const LOG_CAP = 200;
const EVENT_CAP = 300; // bounded — long-session memory safety

const DEFAULT_RUNTIME = {
  status: "idle" as const,
  phase: "",
  bid: null,
  winning: false,
  uptime: "",
  metrics: { cpu: 0, memMb: 0, battery: 100, busLatencyMs: 0 },
  taskQueue: [],
};

export const createRuntimeSlice: StateCreator<RootStore, [], [], RuntimeSlice> = (set) => ({
  actorRuntime: {},
  busMessages: [],
  busRate: 0,
  logLines: [],
  validation: [],
  contracts: [],
  dataSource: "mock",
  linkStatus: "connecting",
  events: [],

  pushBusMessage: (m) =>
    set((s) => ({ busMessages: [...s.busMessages, m].slice(-BUS_CAP) })),

  pushLogLine: (l) =>
    set((s) => ({ logLines: [...s.logLines, l].slice(-LOG_CAP) })),

  pushEvent: (e) =>
    set((s) => {
      // Uniform HH:MM:SS — accept a caller ts only if it already matches,
      // otherwise derive locally (keeps the timeline column aligned).
      const ts = e.ts && /^\d{2}:\d{2}:\d{2}$/.test(e.ts) ? e.ts : new Date().toISOString().slice(11, 19);
      return {
        events: [
          ...s.events,
          { ...e, id: `ev-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`, ts },
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

  setConnection: (source, status) => set(() => ({ dataSource: source, linkStatus: status })),

  hydrateRuntime: (patch) => set(() => ({ ...patch })),
});
