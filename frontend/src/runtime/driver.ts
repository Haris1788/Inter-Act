import type { useStore } from "@/store";

// The single seam between the UI and any data source. Both the mock and the
// socket driver implement this identical interface (targeting the existing,
// unchanged nginx `/socket.io/` + REST proxy).
export interface ActorOpResult {
  ok: boolean;
  /** short, honest human note (shown in the operational event timeline) */
  note: string;
}

export interface RuntimeDriver {
  start(store: typeof useStore): void;
  stop(): void;
  // ── Phase 13 operational controls ──
  // Real actor-scoped runtime control. The live driver calls the backend
  // container endpoints then reconciles UI state from the backend (never
  // advances optimistically). The mock driver mutates the mock runtime
  // honestly (the topbar already shows the data source as "mock").
  stopActor(actorId: string): Promise<ActorOpResult>;
  restartActor(actorId: string): Promise<ActorOpResult>;
  /** re-derive runtime state from the backend (reconnect / post-action sync) */
  refreshRuntime(): Promise<void>;
}
