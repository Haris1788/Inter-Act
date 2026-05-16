import type { StateCreator } from "zustand";
import type { RootStore, RuntimeSlice } from "./types";

const BUS_CAP = 40;
const LOG_CAP = 200;

export const createRuntimeSlice: StateCreator<RootStore, [], [], RuntimeSlice> = (set) => ({
  actorRuntime: {},
  busMessages: [],
  busRate: 0,
  logLines: [],
  validation: [],
  contracts: [],

  pushBusMessage: (m) =>
    set((s) => ({ busMessages: [...s.busMessages, m].slice(-BUS_CAP) })),

  pushLogLine: (l) =>
    set((s) => ({ logLines: [...s.logLines, l].slice(-LOG_CAP) })),

  setActorRuntime: (id, patch) =>
    set((s) => ({
      actorRuntime: {
        ...s.actorRuntime,
        [id]: { ...s.actorRuntime[id], ...patch },
      },
    })),

  hydrateRuntime: (patch) => set(() => ({ ...patch })),
});
