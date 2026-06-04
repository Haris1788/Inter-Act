// Local persistence for the authored workspace: one-shot restore on load
// + debounced autosave + undo/redo history capture. Storage = browser
// localStorage (no cloud, container fs is read-only). The authored
// topology is the USER's document, so it is restored in every mode (mock
// AND live) - the live driver still owns the separate runtime slice.
import { useEffect } from "react";
import { useStore } from "@/store";
import { docToState } from "@/lib/topologySchema";
import { loadLocal } from "@/lib/persistence";
import { saveWorkspace } from "./save";
import { primeHistory, installHistory } from "./history";

// Autosave ~1s after the last change (topology OR workspace name).
const DEBOUNCE_MS = 1000;

export function useAutosave() {
  useEffect(() => {
    let restored = false;
    // History owns its own change subscription, independent of autosave
    // and the Save button - saving never resets undo/redo.
    installHistory();

    // Restore the saved workspace once the runtime link has settled (so we
    // restore on top of, not before, the driver's initial hydrate). Applies
    // in mock AND live: the authored doc is the user's to keep.
    const tryRestore = (): boolean => {
      if (restored) return true;
      const s = useStore.getState();
      const settled =
        s.linkStatus === "connected" || s.linkStatus === "disconnected";
      if (!settled) return false;
      restored = true;
      const doc = loadLocal();
      if (doc && doc.actors.length) {
        s.replaceTopology(docToState(doc));
        s.markClean();
      }
      primeHistory(); // baseline AFTER restore so first undo is meaningful
      return true;
    };

    tryRestore();
    const unsubConn = useStore.subscribe((state, prev) => {
      if (state.linkStatus !== prev.linkStatus) tryRestore();
    });

    let timer: ReturnType<typeof setTimeout> | null = null;
    const unsubSave = useStore.subscribe((state, prev) => {
      if (
        state.actors === prev.actors &&
        state.pool === prev.pool &&
        state.goal === prev.goal &&
        state.workspaceName === prev.workspaceName
      )
        return;
      if (timer) clearTimeout(timer);
      timer = setTimeout(saveWorkspace, DEBOUNCE_MS);
    });

    return () => {
      if (timer) clearTimeout(timer);
      unsubConn();
      unsubSave();
    };
  }, []);
}
