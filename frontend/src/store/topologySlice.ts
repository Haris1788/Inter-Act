import type { StateCreator } from "zustand";
import type { RootStore, TopologySlice } from "./types";

export const createTopologySlice: StateCreator<RootStore, [], [], TopologySlice> = (set) => ({
  actors: {},
  zones: {},
  pool: { id: "pool", bounds: { x: 350, y: 410, w: 620, h: 200 }, label: "Mobile Transport Pool", sub: "· 3 agents · negotiating" },
  goal: { text: "", constraints: {} },
  selectedActorId: null,

  setSelectedActor: (id) => set({ selectedActorId: id }),

  moveActor: (id, position) =>
    set((s) =>
      s.actors[id] ? { actors: { ...s.actors, [id]: { ...s.actors[id], position } } } : {},
    ),

  setGoalText: (text) => set((s) => ({ goal: { ...s.goal, text } })),

  toggleConstraint: (key) =>
    set((s) => ({
      goal: { ...s.goal, constraints: { ...s.goal.constraints, [key]: !s.goal.constraints[key] } },
    })),

  hydrateTopology: (patch) => set(() => ({ ...patch })),
});
