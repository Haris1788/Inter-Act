// Debounced local autosave + one-shot restore of the authored topology.
// Mock mode only restores from localStorage (the live backend is the
// authority when connected - we never override live topology with a cache).
import { useEffect } from "react";
import { useStore } from "@/store";
import { docToState } from "@/lib/topologySchema";
import { loadLocal } from "@/lib/persistence";
import { saveWorkspace } from "./save";

// Autosave ~1s after the last change (topology OR workspace name).
const DEBOUNCE_MS = 1000;

export function useAutosave() {
  useEffect(() => {
    let restored = false;

    // Restore is decided by the driver outcome, not a fixed timer (avoids a
    // race where a slow socket connect lets a stale cache clobber live data):
    //   - dataSource "mock"            → safe to restore the authored cache
    //   - dataSource "live" (any link) → backend is authority, never restore
    const tryRestore = (): boolean => {
      if (restored) return true;
      const s = useStore.getState();
      if (s.dataSource === "live") {
        restored = true; // live wins permanently - abandon restore
        return true;
      }
      if (s.dataSource === "mock" && s.linkStatus === "connected") {
        restored = true;
        const doc = loadLocal();
        if (doc && doc.actors.length) {
          s.replaceTopology(docToState(doc));
          s.markClean();
        }
        return true;
      }
      return false; // not settled yet
    };

    // Attempt immediately, then on every connection-state change until settled.
    tryRestore();
    const unsubConn = useStore.subscribe((state, prev) => {
      if (
        state.dataSource !== prev.dataSource ||
        state.linkStatus !== prev.linkStatus
      ) {
        tryRestore();
      }
    });

    let timer: ReturnType<typeof setTimeout> | null = null;
    const unsubSave = useStore.subscribe((state, prev) => {
      if (
        state.actors === prev.actors &&
        state.zones === prev.zones &&
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
