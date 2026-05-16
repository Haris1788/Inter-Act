import type { StateCreator } from "zustand";
import type { RootStore, TopologySlice, TopologyActor } from "./types";

function uniqueId(base: string, taken: Record<string, unknown>): string {
  if (!taken[base]) return base;
  let n = 2;
  while (taken[`${base}-${n}`]) n++;
  return `${base}-${n}`;
}

export const createTopologySlice: StateCreator<RootStore, [], [], TopologySlice> = (set, get) => ({
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

  addActor: (actor) =>
    set((s) => ({ actors: { ...s.actors, [actor.id]: actor } })),

  removeActor: (id) =>
    set((s) => {
      if (!s.actors[id]) return {};
      const next = { ...s.actors };
      delete next[id];
      // Deletion invariant: editor selection must not reference dead ids.
      return {
        actors: next,
        selectedActorId: s.selectedActorId === id ? null : s.selectedActorId,
        selectedIds: s.selectedIds.filter((x) => x !== id),
      };
    }),

  duplicateActor: (id) => {
    const src = get().actors[id];
    if (!src) return null;
    const newId = uniqueId(`${src.id}-copy`, get().actors);
    const clone: TopologyActor = {
      ...src,
      id: newId,
      position: { x: src.position.x + 36, y: src.position.y + 36 },
    };
    set((s) => ({ actors: { ...s.actors, [newId]: clone } }));
    return newId;
  },

  updateActor: (id, patch) =>
    set((s) =>
      s.actors[id] ? { actors: { ...s.actors, [id]: { ...s.actors[id], ...patch } } } : {},
    ),

  // Wholesale replace (import / load / live takeover). Selection is reset so
  // no editor selectedIds dangle on actors that no longer exist.
  replaceTopology: (patch) =>
    set(() => ({ ...patch, selectedActorId: null, selectedIds: [] })),

  hydrateTopology: (patch) => set(() => ({ ...patch })),
});
