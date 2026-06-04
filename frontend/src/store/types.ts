// Shared store types. Three concerns are kept strictly separate (prompt:
// "Maintain strict separation between Topology / Runtime / Spatial state").

export type ActorId = string;
export type Vec2 = { x: number; y: number };
// Phase 7 Stage A: "zone" added so source/sink zones become first-class actors
// (canvas source-of-truth). Dormant this stage — no creation path emits a
// kind:"zone" actor yet (Stage B wires nodeFactory + onDrop). Additive only.
export type ActorKind = "arm" | "agv" | "zone";
// "down" added in Phase 2 for dead/failed/stopped backend states
// (degraded-state visualization).
export type ActorStatus = "ok" | "busy" | "warn" | "idle" | "down";

// Phase 2 - which driver is feeding the store, and live link health.
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
  /**
   * true  → user placed this actor via drag-drop (transparent RF node,
   *         Konva assembly is the sole visual — before AND after deploy).
   * false → seeded from /config, preset, or runtime hydration (full
   *         ActorNode card).
   * Normalized to an explicit boolean — never undefined at runtime.
   */
  authored: boolean;
  /**
   * Phase 7 Stage A: AGV silhouette ("shuttle"|"forklift"|"tow") OR zone role
   * ("source"|"sink"). Ignored for arms. Optional + additive — existing arm/AGV
   * code never reads it; zone renderers (Stage C) and zone deploy (Stage E) do.
   * E parity: matches E `TopologyActor.subtype`. NOTE divergence — E also carries
   * reach_px/capacity/speed (its Phase 5); NOT adopted here (D never had them,
   * out of Phase 7 scope).
   */
  subtype?: "shuttle" | "forklift" | "tow" | "source" | "sink";
}

/**
 * Phase 7 Stage F: LEGACY DOC-FORMAT TYPE ONLY (documented compat shim).
 * The runtime `zones` slice is retired — zones are `kind:"zone"` TopologyActors.
 * This type survives solely so `TopologyDoc.zones[]` can still be parsed from
 * old saved/exported workspaces and folded into actors on import
 * (topologySchema.docToState). No store slice, selector, or live path uses it.
 */
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
  // Phase 7 Stage F: `zones` slice retired — zones are kind:"zone" actors.
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
    patch: Pick<TopologySlice, "actors" | "pool" | "goal">,
  ) => void;
  hydrateTopology: (
    patch: Partial<Pick<TopologySlice, "actors" | "pool" | "goal">>,
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
export interface ActorRuntime {
  status: ActorStatus;
  phase: string;
  bid: number | null;
  winning: boolean;
  uptime: string;
  metrics: {
    cpu: number;
    memMb: number;
    battery: number;
    busLatencyMs: number;
  };
  taskQueue: {
    id: string;
    ix: string;
    label: string;
    sub: string;
    etaS: number;
    active: boolean;
  }[];
}

// US 34.1: BusKind covers every event type the backend bus emits, so the
// live BusPane is a faithful mirror of what the master/AGV/arm agents say
// to one another. "system" is the fallback for log-derived rows that don't
// carry a real bus envelope.
export type BusKind =
  | "announce"
  | "bid"
  | "accept"
  | "dock"
  | "task_announce"
  | "task_orphan"
  | "claim"
  | "agent_status"
  | "intent"
  | "arm_approach"
  | "arm_ready"
  | "arm_busy"
  | "yield"
  | "help_request"
  | "plan"
  | "task_spec"
  | "reassignment"
  | "system";

export interface BusMessage {
  id: string;
  ts: string;
  kind: BusKind;
  from: string;
  to?: string;
  text: string;
  /** US 34.1: raw bus task_id when present (e.g. "pkg-2"). */
  taskId?: string;
  /** US 34.1: monotonic seq number from the backend ring buffer. */
  seq?: number;
}

// US 34.1: route polyline broadcast by an AGV's intent event. Cells are
// (col, row) integers in the warehouse grid. Color matches the AGV.
export interface AgvRoute {
  agvId: string;
  color: string;
  cells: [number, number][];
  action: string; // "move_to_input" | "traverse_lane" | "move_to_output" | "return_to_bay"
  ts: number; // performance.now() at receipt — used by the layer to fade old routes
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

// US 34.1: which AI tier is currently doing work. Surfaced in the left-panel
// foot so the operator sees "qwq:32b · planning" while the planner is hot,
// "qwen2.5:14b · tactical" while AGVs/arms are evaluating, etc. Derived from
// bus events, not from an explicit backend signal.
export interface ActiveAi {
  model: string; // e.g. "qwq:32b", "qwen2.5:14b", "qwen2.5:0.5b"
  role: string;  // e.g. "planner", "tactical", "judge", "idle"
  source: string; // who last fired ("master", "agv-2", "arm-1", ...)
  tsMs: number;   // performance.now() at last update
}

export interface RuntimeSlice {
  actorRuntime: Record<ActorId, ActorRuntime>;
  busMessages: BusMessage[];
  busRate: number;
  logLines: LogLine[];
  validation: ValidationRow[];
  contracts: { id: string; from: ActorId; to: ActorId }[];
  /** Phase 2 - live link state, shown in the topbar; never persisted. */
  dataSource: DataSource;
  linkStatus: LinkStatus;
  /** Phase 8 - bounded operational event timeline (observability). */
  events: OperationalEvent[];
  /** US 34.1 - per-AGV last announced route, for the canvas polyline. */
  agvRoutes: Record<string, AgvRoute>;
  /** US 34.1 - which AI is currently driving the system. */
  activeAi: ActiveAi;
  pushBusMessage: (m: BusMessage) => void;
  pushLogLine: (l: LogLine) => void;
  pushEvent: (e: Omit<OperationalEvent, "id" | "ts"> & { ts?: string }) => void;
  setActorRuntime: (id: ActorId, patch: Partial<ActorRuntime>) => void;
  setConnection: (source: DataSource, status: LinkStatus) => void;
  /** US 34.1 - replace/clear a per-AGV route polyline. */
  setAgvRoute: (route: AgvRoute) => void;
  clearAgvRoute: (agvId: string) => void;
  /** US 34.1 - update the active-AI label. */
  setActiveAi: (patch: Partial<ActiveAi>) => void;
  hydrateRuntime: (
    patch: Partial<
      Pick<
        RuntimeSlice,
        | "actorRuntime"
        | "busMessages"
        | "logLines"
        | "validation"
        | "contracts"
        | "busRate"
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

/** Sneh-port: unified payload model. One entry per package, carrier resolves
 * to a position. Eliminates the table/arm/AGV triple-render race. */
export interface Payload {
  id: string;
  color: string;
  /** "agv-N" | "arm-N" | "zone:input_arm" | "zone:output_arm" | null */
  carrier: string | null;
  /** Last-known pixel position — used when carrier is a zone or null. */
  lastPos?: Vec2;
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
  /** Packages currently held by each arm (mid-sweep). arm_key → package_id | null. */
  armCargo: Record<string, string | null>;
  /** Sneh-port: unified payload registry, carrier-driven. */
  payloads: Record<string, Payload>;
  tick: (dtMs: number) => void;
  // Phase 2 - runtime-driven, ephemeral. Driven by object_update events.
  setAgvKinematics: (id: ActorId, patch: Partial<AgvKinematics>) => void;
  setAgvTarget: (id: ActorId, target: Vec2 | null, speed?: number) => void;
  setArmAngle: (armId: string, angle: number) => void;
  setTablePkgs: (station: string, pkgs: TablePackage[]) => void;
  setAgvCargo: (agvId: string, packageId: string | null) => void;
  setArmCargo: (armKey: string, packageId: string | null) => void;
  /** Sneh-port: upsert a payload and move it to a new carrier (or null). */
  setPayloadCarrier: (id: string, color: string, carrier: string | null, lastPos?: Vec2) => void;
  clearPayload: (id: string) => void;
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
