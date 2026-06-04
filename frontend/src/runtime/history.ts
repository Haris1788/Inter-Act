// Undo/redo for the authored workspace (topology + name).
//
// Self-contained: it installs ONE zustand subscription and captures the
// PRE-edit snapshot from zustand's `prev`, debounced. This is independent
// of autosave and of the Save button - saving never touches history - and
// it ignores the echo of its own undo/redo apply. History therefore works
// continuously from app start, across any number of saves.

import { useSyncExternalStore } from "react";
import { useStore } from "@/store";
import type { RootStore } from "@/store";
import {
  serializeTopology,
  docToState,
  type TopologyDoc,
} from "@/lib/topologySchema";

interface Snapshot {
  doc: TopologyDoc;
  name: string;
}

const CAP = 100;
const DEBOUNCE_MS = 350;

let past: Snapshot[] = [];
let future: Snapshot[] = [];
let last: Snapshot | null = null; // latest committed state
let pendingPrev: Snapshot | null = null; // pre-edit state of the current burst
let timer: ReturnType<typeof setTimeout> | null = null;
let installed = false;

const listeners = new Set<() => void>();
function emit() {
  for (const l of listeners) l();
}
function subscribe(cb: () => void) {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

function snap(s: Pick<RootStore, "actors" | "pool" | "goal" | "workspaceName">): Snapshot {
  return { doc: serializeTopology(s), name: s.workspaceName };
}

function sameDoc(a: Snapshot, b: Snapshot): boolean {
  // Phase 7 Stage F: zones live inside actors (kind:"zone") — comparing
  // doc.actors already covers them (no separate doc.zones compare).
  return (
    a.name === b.name &&
    JSON.stringify(a.doc.actors) === JSON.stringify(b.doc.actors) &&
    JSON.stringify(a.doc.pool) === JSON.stringify(b.doc.pool) &&
    JSON.stringify(a.doc.goal) === JSON.stringify(b.doc.goal)
  );
}

function topologyChanged(a: RootStore, b: RootStore): boolean {
  return (
    a.actors !== b.actors ||
    a.pool !== b.pool ||
    a.goal !== b.goal ||
    a.workspaceName !== b.workspaceName
  );
}

/** Seed/reset the baseline. Call once after the initial topology (restore
 *  or backend hydrate) has settled so "undo to start" means the loaded
 *  workspace. */
export function primeHistory(): void {
  last = snap(useStore.getState());
  past = [];
  future = [];
  pendingPrev = null;
  emit();
}

/** Install the single change subscription. Idempotent. */
export function installHistory(): void {
  if (installed) return;
  installed = true;
  if (!last) last = snap(useStore.getState());

  useStore.subscribe((state, prev) => {
    if (!topologyChanged(state, prev)) return;
    const cur = snap(state);
    // Echo of our own undo/redo apply (or a no-op write): never record,
    // so the redo stack is preserved across an undo.
    if (last && sameDoc(cur, last)) return;
    // First real edit in this burst: remember the state BEFORE it as the
    // undo target. Subsequent edits in the burst coalesce.
    if (!pendingPrev) pendingPrev = snap(prev);
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => {
      timer = null;
      if (!pendingPrev) return;
      const committed = snap(useStore.getState());
      if (sameDoc(committed, pendingPrev)) {
        pendingPrev = null; // net no-op (edited then reverted)
        return;
      }
      past.push(pendingPrev);
      if (past.length > CAP) past.shift();
      future = [];
      last = committed;
      pendingPrev = null;
      emit();
    }, DEBOUNCE_MS);
  });
}

function apply(s: Snapshot) {
  const st = useStore.getState();
  st.replaceTopology(docToState(s.doc));
  st.setWorkspaceName(s.name);
  last = s; // so the subscription's sameDoc check treats this as an echo
  emit();
}

export function undo(): void {
  if (!past.length) return;
  future.unshift(snap(useStore.getState()));
  apply(past.pop()!);
}

export function redo(): void {
  if (!future.length) return;
  past.push(snap(useStore.getState()));
  apply(future.shift()!);
}

export const canUndo = () => past.length > 0;
export const canRedo = () => future.length > 0;

/** React binding for button enable/disable. */
export function useHistoryState() {
  return useSyncExternalStore(
    subscribe,
    () => `${past.length}:${future.length}`,
    () => "0:0",
  );
}
