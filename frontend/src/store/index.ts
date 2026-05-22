import { create } from "zustand";
import { useStoreWithEqualityFn } from "zustand/traditional";
import type { RootStore } from "./types";
import { createTopologySlice } from "./topologySlice";
import { createRuntimeSlice } from "./runtimeSlice";
import { createSpatialSlice } from "./spatialSlice";
import { createEditorSlice } from "./editorSlice";

// One store, three independently-authored slices. Slice-scoped selectors keep a
// 60fps spatial tick from re-rendering React Flow topology nodes.
export const useStore = create<RootStore>()((...a) => ({
  ...createTopologySlice(...a),
  ...createRuntimeSlice(...a),
  ...createSpatialSlice(...a),
  ...createEditorSlice(...a),
}));

// Equality-fn variant for selectors that return fresh arrays/objects.
export function useStoreEq<T>(
  selector: (s: RootStore) => T,
  equality: (a: T, b: T) => boolean,
): T {
  return useStoreWithEqualityFn(useStore, selector, equality);
}

export type { RootStore };
