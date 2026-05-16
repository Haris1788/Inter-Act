// Fixtures transcribed from the reference visual orchestration workspace
// prototype (INTER.act topology authoring interface).
// Coordinates are in the 1320x720 stage space. Actor `position` is the node
// box top-left (React Flow semantics); zone `position` is the chip center
// (the prototype uses translate(-50%,-50%) on zone chips).
import type {
  TopologyActor,
  TopologyZone,
  TopologyPool,
  TopologyGoal,
  ActorRuntime,
  BusMessage,
  LogLine,
  ValidationRow,
  Trajectory,
  ActorId,
} from "@/store/types";

export const mockActors: Record<ActorId, TopologyActor> = {
  "arm-1": {
    id: "arm-1",
    kind: "arm",
    type: "Arm · 6DoF",
    label: "Source actor",
    // y=165 → card center (165+75) == halo center y=240 (concentric).
    position: { x: 180, y: 165 },
    size: { w: 210, h: 150 },
    capabilities: ["pick", "place", "dock"],
    liveCapability: "pick",
    brain: "Scripted",
    spec: "reach 1.2 m · 5 kg",
  },
  "arm-2": {
    id: "arm-2",
    kind: "arm",
    type: "Arm · SCARA",
    label: "Destination actor",
    // Concentric with the arm-2 halo (center y=240).
    position: { x: 930, y: 165 },
    size: { w: 210, h: 150 },
    capabilities: ["pick", "place", "dock"],
    liveCapability: "place",
    brain: "Scripted",
    spec: "reach 0.9 m · 3 kg",
  },
  "veh-1": {
    id: "veh-1",
    kind: "agv",
    type: "Shuttle AGV",
    label: "Fast lane",
    position: { x: 368, y: 455 },
    size: { w: 184, h: 118 },
    capabilities: ["transit", "dock", "carry"],
    liveCapability: null,
    brain: "Scheduler · FIFO",
    spec: "10 m/s · cap 1",
  },
  "veh-2": {
    id: "veh-2",
    kind: "agv",
    type: "Forklift AGV",
    label: "Heavy load",
    position: { x: 568, y: 455 },
    size: { w: 184, h: 118 },
    capabilities: ["transit", "dock", "carry"],
    liveCapability: null,
    brain: "Scheduler · FIFO",
    spec: "3 m/s · cap 4",
  },
  "veh-3": {
    id: "veh-3",
    kind: "agv",
    type: "Tow AGV",
    label: "Balanced",
    position: { x: 768, y: 455 },
    size: { w: 184, h: 118 },
    capabilities: ["transit", "dock", "carry"],
    liveCapability: null,
    brain: "Scheduler · FIFO",
    spec: "6 m/s · cap 2",
  },
};

export const mockZones: Record<string, TopologyZone> = {
  // Zones sit on the shared centerline (y=240) and clear the arm halos
  // (outer r=170 → edges x=115 / x=1205) with ~40px breathing room. The
  // KonvaOverlay whisper strokes bridge zone → halo.
  "feed-I": {
    id: "feed-I",
    role: "source",
    label: "feed-I",
    position: { x: 55, y: 240 },
    count: "queue · 12",
  },
  "sink-O": {
    id: "sink-O",
    role: "sink",
    label: "sink-O",
    position: { x: 1265, y: 240 },
    count: "received · 0",
  },
};

export const mockPool: TopologyPool = {
  id: "pool",
  bounds: { x: 350, y: 410, w: 620, h: 200 },
  label: "Mobile Transport Pool",
  sub: "· 3 agents · available",
};

export const mockGoal: TopologyGoal = {
  text: "move 7 objects from feed-I to sink-O, optimise for time",
  constraints: { time: true, energy: false, throughput: false },
};

export const mockActorRuntime: Record<ActorId, ActorRuntime> = {
  "arm-1": {
    status: "busy",
    phase: "announcing",
    bid: null,
    winning: false,
    uptime: "up 2h 03m",
    metrics: { cpu: 22, memMb: 120, battery: 100, busLatencyMs: 8 },
    taskQueue: [],
  },
  "arm-2": {
    status: "ok",
    phase: "awaiting",
    bid: null,
    winning: false,
    uptime: "up 2h 03m",
    metrics: { cpu: 19, memMb: 110, battery: 100, busLatencyMs: 8 },
    taskQueue: [],
  },
  "veh-1": {
    status: "busy",
    phase: "approaching arm-1",
    bid: 0.92,
    winning: true,
    uptime: "up 1h 42m",
    metrics: { cpu: 31, memMb: 142, battery: 82, busLatencyMs: 9 },
    taskQueue: [
      {
        id: "t1",
        ix: "▶",
        label: "transit feed-I → arm-1",
        sub: "obj#001",
        etaS: 2.1,
        active: true,
      },
      {
        id: "t2",
        ix: "02",
        label: "dock @ arm-2",
        sub: "obj#001",
        etaS: 5.6,
        active: false,
      },
      {
        id: "t3",
        ix: "03",
        label: "return-to-pool",
        sub: "slot-A",
        etaS: 7.9,
        active: false,
      },
    ],
  },
  "veh-2": {
    status: "idle",
    phase: "available",
    bid: 0.61,
    winning: false,
    uptime: "up 1h 42m",
    metrics: { cpu: 3, memMb: 98, battery: 96, busLatencyMs: 9 },
    taskQueue: [],
  },
  "veh-3": {
    status: "idle",
    phase: "available",
    bid: 0.78,
    winning: false,
    uptime: "up 1h 42m",
    metrics: { cpu: 5, memMb: 101, battery: 88, busLatencyMs: 9 },
    taskQueue: [],
  },
};

export const mockBus: BusMessage[] = [
  {
    id: "b1",
    ts: "14:02:30",
    kind: "announce",
    from: "arm-1",
    to: "*",
    text: "pick @ feed-I",
  },
  {
    id: "b2",
    ts: "14:02:30",
    kind: "bid",
    from: "veh-1",
    text: "0.92 (speed)",
  },
  { id: "b3", ts: "14:02:30", kind: "bid", from: "veh-2", text: "0.61" },
  { id: "b4", ts: "14:02:30", kind: "bid", from: "veh-3", text: "0.78" },
  {
    id: "b5",
    ts: "14:02:31",
    kind: "accept",
    from: "arm-1",
    to: "veh-1",
    text: "",
  },
  {
    id: "b6",
    ts: "14:02:31",
    kind: "dock",
    from: "veh-1",
    text: "approach r=0.4 m",
  },
];

const seg = (text: string, cls?: string) => ({ text, cls });
export const mockLogs: LogLine[] = [
  {
    id: "l1",
    ts: "14:02:28",
    level: "ok",
    segments: [
      seg("[bus] ", "dim"),
      seg("capabilities advertised · "),
      seg("arm-1", "id2"),
      seg(" arm-2 veh-1 veh-2 veh-3"),
    ],
  },
  {
    id: "l2",
    ts: "14:02:29",
    level: "info",
    segments: [
      seg("[goal] ", "dim"),
      seg('intent received · "'),
      seg("move 7 objects from feed-I to sink-O, optimise time", "ok"),
      seg('"'),
    ],
  },
  {
    id: "l3",
    ts: "14:02:30",
    level: "neg",
    segments: [
      seg("arm-1", "id"),
      seg(" announce · capability "),
      seg("pick", "b"),
      seg("@feed-I · seeking transport"),
    ],
  },
  {
    id: "l4",
    ts: "14:02:30",
    level: "neg",
    segments: [
      seg("veh-1", "id2"),
      seg(" 0.92 "),
      seg("(d=2.1m, v=10m/s, bat=82%)", "dim"),
      seg(" · "),
      seg("veh-2", "id2"),
      seg(" 0.61 · "),
      seg("veh-3", "id2"),
      seg(" 0.78"),
    ],
  },
  {
    id: "l5",
    ts: "14:02:31",
    level: "ok",
    segments: [
      seg("arm-1", "id"),
      seg(" → "),
      seg("veh-1", "id2"),
      seg(" · contract "),
      seg("c-7f3a", "dim"),
      seg(" sealed (time-priority)"),
    ],
  },
  {
    id: "l6",
    ts: "14:02:31",
    level: "dock",
    segments: [
      seg("veh-1", "id2"),
      seg(" approaching "),
      seg("arm-1", "id"),
      seg(" · r=0.4m · approach 0.30 m/s"),
    ],
  },
  {
    id: "l7",
    ts: "14:02:33",
    level: "dock",
    segments: [
      seg("veh-1", "id2"),
      seg(" docked @ "),
      seg("arm-1", "id"),
      seg(" · pose locked ±20mm"),
    ],
  },
  {
    id: "l8",
    ts: "14:02:33",
    level: "info",
    segments: [
      seg("arm-1", "id"),
      seg(" picked obj#001 from "),
      seg("feed-I", "dim"),
      seg(" · place-on "),
      seg("veh-1", "id2"),
      seg(" direct"),
    ],
  },
  {
    id: "l9",
    ts: "14:02:34",
    level: "info",
    segments: [
      seg("veh-1", "id2"),
      seg(" undocking · transit to "),
      seg("arm-2", "id"),
      seg(" · est 2.6s"),
    ],
  },
  {
    id: "l10",
    ts: "14:02:34",
    level: "neg",
    segments: [
      seg("arm-2", "id"),
      seg(" announce · capability "),
      seg("place", "b"),
      seg("@sink-O · awaiting carrier"),
    ],
  },
  {
    id: "l11",
    ts: "14:02:36",
    level: "ok",
    segments: [
      seg("arm-2", "id"),
      seg(" placed obj#001 → "),
      seg("sink-O", "ok"),
      seg(" · cycle 5.2s"),
    ],
  },
  {
    id: "l12",
    ts: "14:02:37",
    level: "info",
    segments: [
      seg("veh-1", "id2"),
      seg(" returning to pool · slot-A · 88% bat"),
    ],
  },
  {
    id: "l13",
    ts: "14:02:40",
    level: "warn",
    segments: [
      seg("veh-3", "id2"),
      seg(" reports battery 84% < threshold · soft-priority decrement"),
    ],
  },
  {
    id: "l14",
    ts: "14:02:42",
    level: "ok",
    segments: [
      seg("obj#002 delivered via "),
      seg("veh-2", "id2"),
      seg(" · throughput "),
      seg("2.6 / s", "num"),
      seg(" (rolling)"),
    ],
  },
];

export const mockValidation: ValidationRow[] = [
  {
    id: "v1",
    level: "ok",
    text: "Every actor exposes a non-empty capability set",
  },
  { id: "v2", level: "ok", text: "Bus reachable from all containers" },
  {
    id: "v3",
    level: "ok",
    text: "No type:conveyor · no handover-table · no static routes",
  },
  {
    id: "v4",
    level: "info",
    text: "Capability overlap: place only on arm-2 - single point of placement",
  },
  {
    id: "v5",
    level: "info",
    text: "2 AGVs idle > 30s · consider reducing pool or raising goal rate",
  },
];

// Phantom negotiated trajectory: veh-1 → arm-1 dock, then arm-1 → arm-2 dock.
export const mockTrajectories: Trajectory[] = [
  {
    id: "ph1",
    path: "M 460 460 Q 380 360, 310 318",
    phantom: true,
    opacity: 0.55,
  },
  {
    id: "ph2",
    path: "M 310 318 Q 650 360, 1010 318",
    phantom: false,
    opacity: 0.25,
  },
];

// Thin dotted reachability candidates (any AGV ↔ any arm).
export const mockReachability = [
  { id: "r1", path: "M 460 460 Q 380 380, 320 290", opacity: 0.55 },
  { id: "r2", path: "M 460 460 Q 700 380, 1000 290", opacity: 0.25 },
  { id: "r3", path: "M 660 460 Q 500 380, 340 290", opacity: 0.25 },
  { id: "r4", path: "M 660 460 Q 820 380, 1000 290", opacity: 0.25 },
  { id: "r5", path: "M 860 460 Q 600 380, 340 290", opacity: 0.25 },
  { id: "r6", path: "M 860 460 Q 940 380, 1000 290", opacity: 0.25 },
];

export const mockDockGlows = [
  { id: "g1", at: { x: 310, y: 318 }, intensity: 0.7 },
];

export const mockContracts = [{ id: "c-7f3a", from: "arm-1", to: "veh-1" }];
export const mockBusRate = 12;
