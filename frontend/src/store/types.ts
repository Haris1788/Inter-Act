// Shared store types. Three concerns are kept strictly separate (prompt:
// "Maintain strict separation between Topology / Runtime / Spatial state").

export type ActorId = string;
export type Vec2 = { x: number; y: number };
export type ActorKind = "arm" | "agv";
export type ActorStatus = "ok" | "busy" | "warn" | "idle";

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
  hydrateTopology: (patch: Partial<Pick<TopologySlice, "actors" | "zones" | "pool" | "goal">>) => void;
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
  level: "ok" | "info" | "warn";
  text: string;
}

export interface RuntimeSlice {
  actorRuntime: Record<ActorId, ActorRuntime>;
  busMessages: BusMessage[];
  busRate: number;
  logLines: LogLine[];
  validation: ValidationRow[];
  contracts: { id: string; from: ActorId; to: ActorId }[];
  pushBusMessage: (m: BusMessage) => void;
  pushLogLine: (l: LogLine) => void;
  setActorRuntime: (id: ActorId, patch: Partial<ActorRuntime>) => void;
  hydrateRuntime: (
    patch: Partial<
      Pick<RuntimeSlice, "actorRuntime" | "busMessages" | "logLines" | "validation" | "contracts" | "busRate">
    >,
  ) => void;
}

// ---------- SLICE 3: Spatial Simulation (ephemeral, frame-rate) ----------
export interface AgvKinematics {
  pos: Vec2;
  heading: number;
  speed: number;
  target: Vec2 | null;
}

export interface Trajectory {
  id: string;
  /** SVG path string in stage space */
  path: string;
  phantom: boolean;
  opacity: number;
}

export interface SpatialSlice {
  agvKinematics: Record<ActorId, AgvKinematics>;
  trajectories: Trajectory[];
  dockGlows: { id: string; at: Vec2; intensity: number }[];
  reachability: { id: string; path: string; opacity: number }[];
  tick: (dtMs: number) => void;
  hydrateSpatial: (
    patch: Partial<
      Pick<SpatialSlice, "agvKinematics" | "trajectories" | "dockGlows" | "reachability">
    >,
  ) => void;
}

export type RootStore = TopologySlice & RuntimeSlice & SpatialSlice;
