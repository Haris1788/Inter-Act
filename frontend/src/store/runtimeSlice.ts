import type { StateCreator } from "zustand";
import type { RootStore, RuntimeSlice } from "./types";

const BUS_CAP = 40;
const LOG_CAP = 200;

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

  pushBusMessage: (m) =>
    set((s) => ({ busMessages: [...s.busMessages, m].slice(-BUS_CAP) })),

  pushLogLine: (l) =>
    set((s) => ({ logLines: [...s.logLines, l].slice(-LOG_CAP) })),

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
