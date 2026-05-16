// Undo/redo for the authored workspace (topology + name). Snapshot-based:
// each settled change pushes the PREVIOUS snapshot onto an undo stack.
// Kept out of the zustand store so a 60fps spatial tick can never thrash
// it; React reads canUndo/canRedo via useSyncExternalStore.

import { useSyncExternalStore } from "react";
import { useStore } from "@/store";
import {
  serializeTopology,
  docToState,
  type TopologyDoc,
} from "@/lib/topologySchema";

interface Snapshot {
  doc: TopologyDoc;
  name: string;
}

const CAP = 60;
let past: Snapshot[] = [];
let future: Snapshot[] = [];
let last: Snapshot | null = null;
let suppress = false; // true while applying an undo/redo (don't re-record)

const listeners = new Set<() => void>();
function emit() {
  for (const l of listeners) l();
}
function subscribe(cb: () => void) {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

function snapshot(): Snapshot {
  const s = useStore.getState();
  return { doc: serializeTopology(s), name: s.workspaceName };
}

function sameDoc(a: Snapshot, b: Snapshot): boolean {
  return (
    a.name === b.name &&
    JSON.stringify(a.doc.actors) === JSON.stringify(b.doc.actors) &&
    JSON.stringify(a.doc.zones) === JSON.stringify(b.doc.zones) &&
    JSON.stringify(a.doc.pool) === JSON.stringify(b.doc.pool) &&
    JSON.stringify(a.doc.goal) === JSON.stringify(b.doc.goal)
  );
}

/** Seed the baseline (called once after the initial topology is loaded). */
export function primeHistory(): void {
  last = snapshot();
  past = [];
  future = [];
  emit();
}

/** Record a settled change. The PREVIOUS snapshot becomes an undo step. */
export function recordHistory(): void {
  if (suppress) return;
  const cur = snapshot();
  if (last && sameDoc(last, cur)) return;
  if (last) {
    past.push(last);
    if (past.length > CAP) past.shift();
  }
  future = [];
  last = cur;
  emit();
}

function apply(s: Snapshot) {
  suppress = true;
  const st = useStore.getState();
  st.replaceTopology(docToState(s.doc));
  st.setWorkspaceName(s.name);
  last = s;
  suppress = false;
  emit();
}

export function undo(): void {
  if (!past.length) return;
  const cur = snapshot();
  const prev = past.pop()!;
  future.unshift(cur);
  apply(prev);
}

export function redo(): void {
  if (!future.length) return;
  const cur = snapshot();
  const next = future.shift()!;
  past.push(cur);
  apply(next);
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
