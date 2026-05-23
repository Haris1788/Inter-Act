// Shared store types. Three concerns are kept strictly separate (prompt:
// "Maintain strict separation between Topology / Runtime / Spatial state").

export type ActorId = string;
export type Vec2 = { x: number; y: number };
export type ActorKind = "arm" | "agv";
// "down" added in Phase 2 for dead/failed/stopped backend states
// (degraded-state visualization).
export type ActorStatus = "ok" | "busy" | "warn" | "idle" | "down";

// Phase 2 - which driver is feeding the store, and live link health.
export type DataSource = "mock" | "live";
export type LinkStatus = "connecting" | "connected" | "disconnected";

// ---------- SLICE 1: Topology (user-composed; persisted to YAML later) ----------
// Phase 5 Stage 2A schema bump (2026-05-23):
//   Added: reach_px (default 120), capacity (default 1), speed (default 1.0).
//   Migration rule: old localStorage workspaces lacking these fields get
//   defaults injected by normalizeActor (called in docToState on every load).
//   Shape is additive — no existing field renamed or removed.
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
  // ── Phase 5 spatial fields ──────────────────────────────────────────────
  /** Arm reach radius in canvas pixels. Meaningful for arms; kept on AGVs
   *  (default 120) for a symmetric shape. Backend uses this to determine
   *  which dock positions are reachable. */
  reach_px: number;
  /** Maximum simultaneous payload capacity (integer ≥ 1). */
  capacity: number;
  /** Speed multiplier (0.5 = slow … 3.0 = fast, step 0.5). Default 1.0. */
  speed: number;
  // ── Phase 5.1 Stage 3: per-kind AGV silhouette ─────────────────────────
  /** AGV vehicle subtype. Ignored for arms. Default "shuttle" (back-compat). */
  subtype?: "shuttle" | "forklift" | "tow";
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
  replaceTopology: (
    patch: Pick<TopologySlice, "actors" | "zones" | "pool" | "goal">,
  ) => void;
  hydrateTopology: (
    patch: Partial<Pick<TopologySlice, "actors" | "zones" | "pool" | "goal">>,
  ) => void;
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
  // (serialized TopologyDoc JSON) - the baseline for divergence detection.
  deployedJson: string | null;
  deployedAt: string | null;
  deployPhase: DeployPhase;
  deployMessage: string;
  // Phase 14 - operational dock collapse. UI chrome only (not persisted to
  // runtime). The whole dock collapses to a slim header strip; canvas takes
  // the freed vertical space. logsHeight restores the pre-collapse size.
  logsCollapsed: boolean;
  logsHeight: number;
  toggleLogsCollapsed: () => void;
  setLogsHeight: (px: number) => void;
  // Editable workspace name (single authoritative source; persisted by
  // save/autosave, not by the component). Renaming marks the doc dirty.
  workspaceName: string;
  setWorkspaceName: (name: string) => void;
  setMode: (m: EditorMode) => void;
  setSelection: (ids: ActorId[]) => void;
  toggleSelection: (id: ActorId, additive: boolean) => void;
  clearSelection: () => void;
  openContextMenu: (m: ContextMenuState) => void;
  closeContextMenu: () => void;
  markDirty: () => void;
  markClean: () => void;
  recordDeploy: (docJson: string) => void;
  clearDeploySnapshot: () => void;
  setDeployPhase: (phase: DeployPhase, message?: string) => void;
  // Phase 7 - request the canvas to center/zoom an actor (nonce so repeat
  // focus of the same id still fires). Consumed by the RF viewport effect.
  focusRequest: { id: ActorId; nonce: number } | null;
  requestFocus: (id: ActorId) => void;
}

// ---------- SLICE 2: Runtime (live status; read-mostly in UI) ----------

/** Observational AGV position emitted by the backend at ~10 Hz.
 *  Never sent back. Authored position (canvas drag) is untouched. */
export interface AgvRuntimePosition {
  x: number;
  y: number;
  /** Heading in radians (atan2 convention). */
  theta: number;
  /** Backend timestamp (epoch ms). */
  ts: number;
}

export interface ActorRuntime {
  status: ActorStatus;
  phase: string;
  uptime: string;
  metrics: {
    cpu: number;
    memMb: number;
    battery: number;
    busLatencyMs: number;
  };
  /** Stage 5 Track B — live pixel position from `agv_position` SocketIO event.
   *  Present only while an AGV is in spatial motion. Observational only. */
  runtimePosition?: AgvRuntimePosition;
  /** Phase 5.1 Stage 5 Track A — latest negotiation state from availability_update
   *  bus messages. One of: "idle" | "bidding" | "won" | "executing" | "done" | "faulted".
   *  Absent until the first heartbeat arrives. */
  negotiationState?: string;
}

// Real NegotiationBus message types — sourced directly from the backend
// Message.type field. No regex inference.
// Stage 4 added: leg_started, leg_completed, leg_halted.
export type BusKind =
  | "task_announce"
  | "capability_broadcast"
  | "availability_update"
  | "bid"
  | "counter_offer"
  | "accept"
  | "reject"
  | "busy"
  | "fault"
  | "withdraw"
  | "consensus_reached"
  | "task_failed"
  | "leg_started"
  | "leg_completed"
  | "leg_halted";

export interface BusMessage {
  id: string;
  ts: string;
  kind: BusKind;
  from: string;
  to?: string;
  /** negotiation round number (> 0 on reopen) */
  round?: number;
  task_id?: string;
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

// Phase 8 - structured operational event (observability). Derived from real
// socket/deploy transitions; NOT fabricated analytics. Bounded ring buffer.
export type EventKind =
  | "health"
  | "asset"
  | "dock"
  | "object"
  | "deploy"
  | "link"
  | "lifecycle";
export interface OperationalEvent {
  id: string;
  ts: string;
  kind: EventKind;
  actorId?: ActorId;
  text: string;
  tone: "ok" | "warn" | "err" | "info";
}

/** Phase 5 Stage 8 Track C — historical AGV position for a single replay frame. */
export interface ReplayPosition {
  x: number;
  y: number;
  theta: number;
}

// ---------- Phase 5.1 Stage 4: AGV path preview ----------
/** One planned path for a single AGV, emitted by backend agv_path event. */
export interface AgvPath {
  /** Grid cells [[col, row], ...] from A*. */
  cells: [number, number][];
  /** Epoch counter — monotonic per (task_id, actor_id). */
  epoch: number;
  /** Backend wall-clock timestamp (epoch seconds). */
  ts: number;
}

export interface RuntimeSlice {
  actorRuntime: Record<ActorId, ActorRuntime>;
  busMessages: BusMessage[];
  logLines: LogLine[];
  validation: ValidationRow[];
  /** Phase 2 - live link state, shown in the topbar; never persisted. */
  dataSource: DataSource;
  linkStatus: LinkStatus;
  /** Phase 8 - bounded operational event timeline (observability). */
  events: OperationalEvent[];
  /** Phase 5 Stage 8 Track C — replay scrub state.
   *  Observational only: never published back to the backend.
   *  When replayMode is true, AGV tokens snap to replayPositions
   *  (skipping tween) rather than reading live runtimePosition/kinematics. */
  replayMode: boolean;
  replayPositions: Record<ActorId, ReplayPosition>;
  pushBusMessage: (m: BusMessage) => void;
  pushLogLine: (l: LogLine) => void;
  pushEvent: (e: Omit<OperationalEvent, "id" | "ts"> & { ts?: string }) => void;
  setActorRuntime: (id: ActorId, patch: Partial<ActorRuntime>) => void;
  /** Stage 5 Track B — update live pixel position for a single AGV.
   *  Does NOT touch authored (canvas) position. */
  setRuntimePosition: (id: ActorId, pos: AgvRuntimePosition) => void;
  setConnection: (source: DataSource, status: LinkStatus) => void;
  /** Phase 5 Stage 8 Track C — enter replay mode with computed positions. */
  setReplayPositions: (positions: Record<ActorId, ReplayPosition>) => void;
  /** Phase 5 Stage 8 Track C — exit replay mode, return to live. */
  clearReplayMode: () => void;
  // ---------- Phase 5.1 Stage 4: AGV path preview ----------
  /** Live planned paths per AGV, keyed by actor_id. Populated by agv_path SocketIO event. */
  agvPaths: Record<ActorId, AgvPath>;
  /** Store or supersede a planned path for one AGV.
   *  Ignores the update when payload.epoch <= current stored epoch. */
  setAgvPath: (actorId: ActorId, path: AgvPath) => void;
  /** Remove the planned path entry for one AGV (called on leg_completed). */
  clearAgvPath: (actorId: ActorId) => void;
  hydrateRuntime: (
    patch: Partial<
      Pick<
        RuntimeSlice,
        | "actorRuntime"
        | "busMessages"
        | "logLines"
        | "validation"
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
  /** leg start - set when a target is assigned; enables accel/decel easing */
  origin?: Vec2 | null;
}

export interface Trajectory {
  id: string;
  /** SVG path string in stage space */
  path: string;
  phantom: boolean;
  opacity: number;
  /** Phase 2 - runtime traces fade out; null = persistent (mock). */
  fadeMsLeft?: number | null;
}

export interface DockGlow {
  id: string;
  at: Vec2;
  intensity: number;
  fadeMsLeft?: number | null;
}

export interface TablePackage {
  id: string;
  color: string;
}

export interface SpatialSlice {
  agvKinematics: Record<ActorId, AgvKinematics>;
  trajectories: Trajectory[];
  dockGlows: DockGlow[];
  reachability: { id: string; path: string; opacity: number }[];
  /** Arm angles in degrees (0=right, 90=down, 180=left, 270=up). Default 270. */
  armAngles: Record<string, number>;
  /** Packages currently visible on each arm's table. station → list. */
  tablePkgs: Record<string, TablePackage[]>;
  /** Packages currently carried by each AGV. agv_id → package_id | null. */
  agvCargo: Record<string, string | null>;
  tick: (dtMs: number) => void;
  // Phase 2 - runtime-driven, ephemeral. Driven by object_update events.
  setAgvKinematics: (id: ActorId, patch: Partial<AgvKinematics>) => void;
  setAgvTarget: (id: ActorId, target: Vec2 | null, speed?: number) => void;
  setArmAngle: (armId: string, angle: number) => void;
  setTablePkgs: (station: string, pkgs: TablePackage[]) => void;
  setAgvCargo: (agvId: string, packageId: string | null) => void;
  emitTrajectory: (t: Trajectory) => void;
  emitDockGlow: (g: DockGlow) => void;
  clearSpatialRuntime: () => void;
  hydrateSpatial: (
    patch: Partial<
      Pick<
        SpatialSlice,
        "agvKinematics" | "trajectories" | "dockGlows" | "reachability"
      >
    >,
  ) => void;
}

export type RootStore = TopologySlice &
  RuntimeSlice &
  SpatialSlice &
  EditorSlice;
