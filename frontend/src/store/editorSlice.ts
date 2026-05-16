import type { StateCreator } from "zustand";
import type { RootStore, EditorSlice } from "./types";

// Authoring/UI state. Independent of runtime + spatial slices (the strict
// topology/runtime/spatial separation is unaffected — this is editor chrome).
export const createEditorSlice: StateCreator<RootStore, [], [], EditorSlice> = (set) => ({
  mode: "select",
  selectedIds: [],
  contextMenu: null,
  dirty: false,

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
});
