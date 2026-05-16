// Shared store types. Three concerns are kept strictly separate (prompt:
// "Maintain strict separation between Topology / Runtime / Spatial state").

export type ActorId = string;
export type Vec2 = { x: number; y: number };
export type ActorKind = "arm" | "agv";
// "down" added in Phase 2 for dead/failed/stopped backend states
// (degraded-state visualization).
export type ActorStatus = "ok" | "busy" | "warn" | "idle" | "down";

// Phase 2 — which driver is feeding the store, and live link health.
export type DataSource = "mock" | "live";
export type LinkStatus = "connecting" | "connected" | "disconnected";

// ---------- SLICE 1: Topology (user-composed; persisted to YAML later) ----------
export interface TopologyActor {
  id: ActorId;
  kind: ActorKind;
  /** display type, e.g. "Arm · 6DoF", "Shuttle AGV" */
  type: string;
  label: string;
  /** authored stage-space coords (== prototype left/top of the node) */
  position: Vec2;
  /** node box size, used for React Flow node + Konva anchoring */
  size: { w: number; h: number };
  capabilities: string[];
  /** which capabilities are currently the "live" one (highlighted) */
  liveCapability: string | null;
  brain: string;
  spec: string;
}

export interface TopologyZone {
  id: string;
  role: "source" | "sink";
  label: string;
  position: Vec2;
  count: string;
}

export interface TopologyPool {
  id: string;
  /** stadium bounds in stage space */
  bounds: { x: number; y: number; w: number; h: number };
  label: string;
  sub: string;
}

export interface TopologyGoal {
  text: string;
  constraints: Record<string, boolean>;
}

export interface TopologySlice {
  actors: Record<ActorId, TopologyActor>;
  zones: Record<string, TopologyZone>;
  pool: TopologyPool;
  goal: TopologyGoal;
  selectedActorId: ActorId | null;
  setSelectedActor: (id: ActorId | null) => void;
  moveActor: (id: ActorId, position: Vec2) => void;
  setGoalText: (text: string) => void;
  toggleConstraint: (key: string) => void;
  // ── Phase 4 authoring mutations (frontend only; no backend) ──
  addActor: (actor: TopologyActor) => void;
  removeActor: (id: ActorId) => void;
  duplicateActor: (id: ActorId) => ActorId | null;
  updateActor: (id: ActorId, patch: Partial<TopologyActor>) => void;
  /** wholesale replace (import / load) */
  replaceTopology: (patch: Pick<TopologySlice, "actors" | "zones" | "pool" | "goal">) => void;
  hydrateTopology: (patch: Partial<Pick<TopologySlice, "actors" | "zones" | "pool" | "goal">>) => void;
}

// ---------- SLICE 4: Editor / Authoring UI (Phase 4) ----------
// Distinct from runtime/spatial. Holds interaction mode + multi-selection +
// context menu + unsaved-changes flag. Never persisted to runtime.
export type EditorMode = "select" | "pan" | "edit";
export interface ContextMenuState {
  x: number;
  y: number;
  nodeId: ActorId | null;
}
// Phase 5 deployment lifecycle (frontend; backend stays the authority).
export type DeployPhase =
  | "idle"
  | "validating"
  | "preparing"
  | "restarting"
  | "reconciling"
  | "prepared"
  | "ready"
  | "failed";

export interface EditorSlice {
  mode: EditorMode;
  selectedIds: ActorId[];
  contextMenu: ContextMenuState | null;
  dirty: boolean;
  // Lifecycle: snapshot of the topology at the last successful deploy/prepare
  // (serialized TopologyDoc JSON) — the baseline for divergence detection.
  deployedJson: string | null;
  deployedAt: string | null;
  deployPhase: DeployPhase;
  deployMessage: string;
  setMode: (m: EditorMode) => void;
  setSelection: (ids: ActorId[]) => void;
  toggleSelection: (id: ActorId, additive: boolean) => void;
  clearSelection: () => void;
  openContextMenu: (m: ContextMenuState) => void;
  closeContextMenu: () => void;
  markDirty: () => void;
  markClean: () => void;
  recordDeploy: (docJson: string) => void;
  setDeployPhase: (phase: DeployPhase, message?: string) => void;
  // Phase 7 — request the canvas to center/zoom an actor (nonce so repeat
  // focus of the same id still fires). Consumed by the RF viewport effect.
  focusRequest: { id: ActorId; nonce: number } | null;
  requestFocus: (id: ActorId) => void;
}

// ---------- SLICE 2: Runtime (live status; read-mostly in UI) ----------
export interface ActorRuntime {
  status: ActorStatus;
  phase: string;
  bid: number | null;
  winning: boolean;
  uptime: string;
  metrics: { cpu: number; memMb: number; battery: number; busLatencyMs: number };
  taskQueue: { id: string; ix: string; label: string; sub: string; etaS: number; active: boolean }[];
}

export type BusKind = "announce" | "bid" | "accept" | "dock";
export interface BusMessage {
  id: string;
  ts: string;
  kind: BusKind;
  from: string;
  to?: string;
  text: string;
}

export interface LogLine {
  id: string;
  ts: string;
  level: "info" | "ok" | "warn" | "neg" | "dock" | "dbg";
  /** pre-rendered rich segments for the mono log stream */
  segments: { text: string; cls?: string }[];
}

export interface ValidationRow {
  id: string;
  level: "ok" | "info" | "warn" | "error";
  text: string;
}

export interface RuntimeSlice {
  actorRuntime: Record<ActorId, ActorRuntime>;
  busMessages: BusMessage[];
  busRate: number;
  logLines: LogLine[];
  validation: ValidationRow[];
  contracts: { id: string; from: ActorId; to: ActorId }[];
  /** Phase 2 — live link state, shown in the topbar; never persisted. */
  dataSource: DataSource;
  linkStatus: LinkStatus;
  pushBusMessage: (m: BusMessage) => void;
  pushLogLine: (l: LogLine) => void;
  setActorRuntime: (id: ActorId, patch: Partial<ActorRuntime>) => void;
  setConnection: (source: DataSource, status: LinkStatus) => void;
  hydrateRuntime: (
    patch: Partial<
      Pick<
        RuntimeSlice,
        "actorRuntime" | "busMessages" | "logLines" | "validation" | "contracts" | "busRate"
      >
    >,
  ) => void;
}

// ---------- SLICE 3: Spatial Simulation (ephemeral, frame-rate) ----------
export interface AgvKinematics {
  pos: Vec2;
  heading: number;
  speed: number;
  target: Vec2 | null;
  /** leg start — set when a target is assigned; enables accel/decel easing */
  origin?: Vec2 | null;
}

export interface Trajectory {
  id: string;
  /** SVG path string in stage space */
  path: string;
  phantom: boolean;
  opacity: number;
  /** Phase 2 — runtime traces fade out; null = persistent (mock). */
  fadeMsLeft?: number | null;
}

export interface DockGlow {
  id: string;
  at: Vec2;
  intensity: number;
  fadeMsLeft?: number | null;
}

export interface SpatialSlice {
  agvKinematics: Record<ActorId, AgvKinematics>;
  trajectories: Trajectory[];
  dockGlows: DockGlow[];
  reachability: { id: string; path: string; opacity: number }[];
  tick: (dtMs: number) => void;
  // Phase 2 — runtime-driven, ephemeral. Driven by object_update events.
  setAgvKinematics: (id: ActorId, patch: Partial<AgvKinematics>) => void;
  setAgvTarget: (id: ActorId, target: Vec2 | null, speed?: number) => void;
  emitTrajectory: (t: Trajectory) => void;
  emitDockGlow: (g: DockGlow) => void;
  clearSpatialRuntime: () => void;
  hydrateSpatial: (
    patch: Partial<
      Pick<SpatialSlice, "agvKinematics" | "trajectories" | "dockGlows" | "reachability">
    >,
  ) => void;
}

export type RootStore = TopologySlice & RuntimeSlice & SpatialSlice & EditorSlice;
