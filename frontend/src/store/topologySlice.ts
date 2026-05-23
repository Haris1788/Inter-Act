// Phase 5 Stage 2A — topology slice schema bump (2026-05-23)
// ──────────────────────────────────────────────────────────────────────────────
// TopologyActor gained three spatial fields: reach_px, capacity, speed.
// All three are optional in ActorSeed and receive defaults inside normalizeActor
// so every creation path (drag-drop, duplicate, import, localStorage restore)
// is covered without touching call sites.
//
// localStorage migration strategy:
//   loadLocal() → parseTopology() → docToState() → normalizeActor() per actor.
//   normalizeActor already fills `?? DEFAULT` for the three new fields, so
//   old workspaces missing them get defaults on every load. Additive + idempotent.
//
// x/y position wiring:
//   moveActor() updates actor.position.{x,y} — called by handleNodesChange in
//   CanvasViewport.tsx on every RF "position" change event (line 80-84).
//   No changes needed to existing drag behaviour; Stage 2A is a pure schema
//   extension.
// ──────────────────────────────────────────────────────────────────────────────

import type { StateCreator } from "zustand";
import type { RootStore, TopologySlice, TopologyActor } from "./types";
import { normalizeActor } from "@/lib/actorNormalizer";

function uniqueId(base: string, taken: Record<string, unknown>): string {
  if (!taken[base]) return base;
  let n = 2;
  while (taken[`${base}-${n}`]) n++;
  return `${base}-${n}`;
}

export const createTopologySlice: StateCreator<RootStore, [], [], TopologySlice> = (set, get) => ({
  actors: {},
  zones: {},
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
    // Through the one canonical normalizer like every other creation path.
    // An authored label (≠ id) is preserved; an id-derived one re-derives.
    const clone: TopologyActor = normalizeActor({
      ...src,
      id: newId,
      position: { x: src.position.x + 36, y: src.position.y + 36 },
    });
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
