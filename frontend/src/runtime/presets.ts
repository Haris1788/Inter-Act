// Demonstration topology presets + safe demo reset. Honest: these are
// authored topology compositions (actors/zones), NOT orchestration AI.
// Reuses the schema/store actions; adds no new system.
import { useStore } from "@/store";
import type {
  TopologyActor,
  TopologyZone,
  TopologyPool,
  TopologyGoal,
} from "@/store/types";
import { mockActors, mockZones, mockPool, mockGoal } from "./mockData";
import { normalizeActor } from "@/lib/actorNormalizer";

interface PresetTopology {
  actors: Record<string, TopologyActor>;
  zones: Record<string, TopologyZone>;
  pool: TopologyPool;
  goal: TopologyGoal;
}

export interface Preset {
  id: string;
  label: string;
  description: string;
  build: () => PresetTopology;
}

const ARM = { w: 210, h: 150 };
const AGV = { w: 184, h: 118 };

function arm(
  id: string,
  type: string,
  label: string,
  x: number,
  y: number,
  spec: string,
): TopologyActor {
  return normalizeActor({
    id,
    kind: "arm",
    type,
    label,
    position: { x, y },
    size: ARM,
    spec,
  });
}
function agv(
  id: string,
  type: string,
  label: string,
  x: number,
  y: number,
  spec: string,
): TopologyActor {
  return normalizeActor({
    id,
    kind: "agv",
    type,
    label,
    position: { x, y },
    size: AGV,
    spec,
  });
}
// Authored presets have no live feed yet - neutral counts, not fabricated.
const ZONES: Record<string, TopologyZone> = {
  // Match mock layout: on the centerline, clear of the arm halos.
  "feed-I": {
    id: "feed-I",
    role: "source",
    label: "feed-I",
    position: { x: 55, y: 240 },
    count: "queue · -",
  },
  "sink-O": {
    id: "sink-O",
    role: "sink",
    label: "sink-O",
    position: { x: 1265, y: 240 },
    count: "received · -",
  },
};
const poolFor = (nAgv: number): TopologyPool => ({
  ...mockPool,
  sub: `· ${nAgv} agent${nAgv === 1 ? "" : "s"} · available`,
});
const GOAL: TopologyGoal = {
  text: "move objects from feed-I to sink-O",
  constraints: { time: true, energy: false, throughput: false },
};

export const PRESETS: Preset[] = [
  {
    id: "demo-cell",
    label: "Demo · transport cell",
    description: "2 arms + 3 AGV pool - the standard demonstration topology.",
    build: () => ({
      actors: { ...mockActors },
      zones: { ...mockZones },
      pool: { ...mockPool },
      goal: { ...mockGoal },
    }),
  },
  {
    id: "minimal",
    label: "Minimal · 1 arm + 1 AGV",
    description: "Smallest coherent topology: a source arm and one shuttle.",
    build: () => ({
      actors: {
        "arm-1": arm(
          "arm-1",
          "Arm · 6DoF",
          "Source actor",
          180,
          165,
          "reach 1.2 m · 5 kg",
        ),
        "veh-1": agv(
          "veh-1",
          "Shuttle AGV",
          "Fast lane",
          560,
          455,
          "10 m/s · cap 1",
        ),
      },
      zones: { ...ZONES },
      pool: poolFor(1),
      goal: GOAL,
    }),
  },
  {
    id: "dual-pool",
    label: "Dual arm · 4-AGV pool",
    description: "Two arms with a larger mobile-agent pool - density demo.",
    build: () => ({
      actors: {
        "arm-1": arm(
          "arm-1",
          "Arm · 6DoF",
          "Source actor",
          180,
          165,
          "reach 1.2 m · 5 kg",
        ),
        "arm-2": arm(
          "arm-2",
          "Arm · SCARA",
          "Destination actor",
          930,
          165,
          "reach 0.9 m · 3 kg",
        ),
        "veh-1": agv(
          "veh-1",
          "Shuttle AGV",
          "Fast lane",
          320,
          455,
          "10 m/s · cap 1",
        ),
        "veh-2": agv(
          "veh-2",
          "Forklift AGV",
          "Heavy load",
          500,
          455,
          "3 m/s · cap 4",
        ),
        "veh-3": agv("veh-3", "Tow AGV", "Balanced", 680, 455, "6 m/s · cap 2"),
        "veh-4": agv(
          "veh-4",
          "Shuttle AGV",
          "Spare lane",
          860,
          455,
          "10 m/s · cap 1",
        ),
      },
      zones: { ...ZONES },
      pool: poolFor(4),
      goal: GOAL,
    }),
  },
  {
    id: "empty",
    label: "Empty workspace",
    description: "No actors - start composing a topology from scratch.",
    build: () => ({
      actors: {},
      zones: { ...ZONES },
      pool: poolFor(0),
      goal: { text: "", constraints: {} },
    }),
  },
];

/** Load a preset: replaces topology + clears ephemeral runtime traces and the
 *  deploy snapshot so the demo starts from a clean, honest state. */
export function applyPreset(id: string): void {
  const p = PRESETS.find((x) => x.id === id);
  if (!p) return;
  const s = useStore.getState();
  const t = p.build();
  s.replaceTopology(t);
  s.clearSpatialRuntime();
  s.clearDeploySnapshot();
  s.setDeployPhase("idle", "");
  s.markClean();
}

/** Demonstration-safe reset to the standard demo cell. */
export function resetDemo(): void {
  applyPreset("demo-cell");
}
