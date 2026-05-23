// Snap-to-grid preference — a focused localStorage hook for Track C.
// Kept outside the Zustand store to avoid touching the forbidden slice files
// (types.ts / editorSlice.ts) while concurrent agents are active.
//
// Default: ON. Persisted under "interact.snap.enabled".

import { useState, useCallback, useEffect } from "react";

const SNAP_KEY = "interact.snap.enabled";
export const CELL_PX = 40;

function readPref(): boolean {
  try {
    const raw = localStorage.getItem(SNAP_KEY);
    // null = not set → default ON; explicit "false" → OFF
    return raw === null ? true : raw !== "false";
  } catch {
    return true;
  }
}

function writePref(v: boolean): void {
  try {
    localStorage.setItem(SNAP_KEY, v ? "true" : "false");
  } catch {
    // storage unavailable - preference persists for this session only
  }
}

/** Returns [snapEnabled, toggleSnap] — reactive, persisted to localStorage. */
export function useSnapPreference(): [boolean, () => void] {
  const [snapEnabled, setSnapEnabled] = useState<boolean>(readPref);

  const toggleSnap = useCallback(() => {
    setSnapEnabled((prev) => {
      const next = !prev;
      writePref(next);
      return next;
    });
  }, []);

  // Sync across tabs (rare but correct).
  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key === SNAP_KEY) setSnapEnabled(readPref());
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  return [snapEnabled, toggleSnap];
}
