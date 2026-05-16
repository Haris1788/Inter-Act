import type { StateCreator } from "zustand";
import type { RootStore, EditorSlice } from "./types";

// Authoring/UI state. Independent of runtime + spatial slices (the strict
// topology/runtime/spatial separation is unaffected — this is editor chrome).
export const createEditorSlice: StateCreator<RootStore, [], [], EditorSlice> = (set) => ({
  mode: "select",
  selectedIds: [],
  contextMenu: null,
  dirty: false,
  deployedJson: null,
  deployedAt: null,
  deployPhase: "idle",
  deployMessage: "",

  setMode: (mode) => set({ mode, contextMenu: null }),

  setSelection: (ids) => set({ selectedIds: ids }),

  toggleSelection: (id, additive) =>
    set((s) => {
      if (!additive) return { selectedIds: [id] };
      return s.selectedIds.includes(id)
        ? { selectedIds: s.selectedIds.filter((x) => x !== id) }
        : { selectedIds: [...s.selectedIds, id] };
    }),

  clearSelection: () => set({ selectedIds: [] }),

  openContextMenu: (m) => set({ contextMenu: m }),
  closeContextMenu: () => set({ contextMenu: null }),

  markDirty: () => set((s) => (s.dirty ? {} : { dirty: true })),
  markClean: () => set((s) => (s.dirty ? { dirty: false } : {})),

  // Records the deploy/prepare baseline for divergence detection. Does NOT
  // clear `dirty` — that flag is local-persistence state; deploy-needed is
  // derived from snapshot divergence, not from autosave.
  recordDeploy: (docJson) =>
    set({ deployedJson: docJson, deployedAt: new Date().toISOString() }),

  setDeployPhase: (deployPhase, message) =>
    set({ deployPhase, deployMessage: message ?? "" }),
});
