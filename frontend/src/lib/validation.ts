// Topology validation - pure, frontend engineering checks (NO AI, NO routing).
// Decentralised model: actors + zones; no handover/transfer pads.
import type { TopologyActor, TopologyZone, ValidationRow } from "@/store/types";

export interface ValidationReport {
  rows: ValidationRow[];
  errors: number;
  warnings: number;
  /** safe to prepare a deploy payload */
  deployable: boolean;
}

type Input = {
  actors: Record<string, TopologyActor>;
  zones: Record<string, TopologyZone>;
};

export function validateTopology({ actors, zones }: Input): ValidationReport {
  const rows: ValidationRow[] = [];
  const list = Object.values(actors);
  const arms = list.filter((a) => a.kind === "arm");
  const agvs = list.filter((a) => a.kind === "agv");

  // Zones come from TWO sources after the Phase 5.1 zone-marker fix:
  //   - the legacy separate `zones` slice (kept for back-compat), and
  //   - actor entries with `kind === "zone"` (current canvas-drop path).
  // Merge both so the deploy panel + warnings see every zone the user
  // dropped, regardless of which path created it.
  const zoneActors = list.filter((a) => a.kind === "zone");
  const zoneList = [
    ...Object.values(zones),
    // Project zone-kind actors onto the legacy { id, role, label } shape so
    // downstream role checks (.role === "source" / "sink") keep working.
    ...zoneActors.map((a) => ({
      id: a.id,
      role: a.subtype === "sink" ? "sink" : "source",
      label: a.label ?? a.id,
      position: a.position,
    })),
  ];

  // Functional actors only (not zone markers).
  const functionalList = list.filter((a) => a.kind !== "zone");

  // ── errors ──────────────────────────────────────────────────────────────
  if (functionalList.length === 0) {
    rows.push({
      id: "v-empty",
      level: "warn",
      text: "Empty topology - add actors to compose an orchestration",
    });
  }

  // duplicate ids (import can introduce; factory cannot) - a hard error
  const seen = new Set<string>();
  for (const a of list) {
    if (seen.has(a.id))
      rows.push({
        id: `v-dup-${a.id}`,
        level: "error",
        text: `Duplicate actor id: ${a.id}`,
      });
    seen.add(a.id);
  }

  // Zones are structural markers, not functional actors — skip capability check.
  for (const a of list.filter((x) => x.kind !== "zone")) {
    if (!a.capabilities || a.capabilities.length === 0)
      rows.push({
        id: `v-cap-${a.id}`,
        level: "warn",
        text: `${a.id} exposes no capabilities`,
      });
  }
  for (const a of arms) {
    const c = a.capabilities ?? [];
    if (!c.includes("pick") && !c.includes("place"))
      rows.push({
        id: `v-armcap-${a.id}`,
        level: "warn",
        text: `${a.id} (arm) has neither pick nor place`,
      });
  }

  // ── warnings / hints ────────────────────────────────────────────────────
  if (functionalList.length > 0 && !zoneList.some((z) => z.role === "source"))
    rows.push({
      id: "v-src",
      level: "warn",
      text: "No source zone - nothing feeds the orchestration",
    });
  if (functionalList.length > 0 && !zoneList.some((z) => z.role === "sink"))
    rows.push({
      id: "v-sink",
      level: "warn",
      text: "No sink zone - nowhere to deliver",
    });
  if (arms.length > 0 && agvs.length === 0)
    rows.push({
      id: "v-noagv",
      level: "info",
      text: "No mobile agents - arms cannot hand off without an AGV",
    });

  // ── ok / info ───────────────────────────────────────────────────────────
  if (functionalList.length > 0 && rows.every((r) => r.level === "info"))
    rows.unshift({
      id: "v-ok",
      level: "ok",
      text: "Every actor exposes a non-empty capability set",
    });
  rows.push({
    id: "v-count",
    level: "info",
    text: `${arms.length} arm${arms.length !== 1 ? "s" : ""} · ${agvs.length} AGV${agvs.length !== 1 ? "s" : ""} · ${zoneList.length} zone${zoneList.length !== 1 ? "s" : ""}`,
  });

  const errors = rows.filter((r) => r.level === "error").length;
  const warnings = rows.filter((r) => r.level === "warn").length;
  const deployable = functionalList.length > 0 && errors === 0;

  return { rows, errors, warnings, deployable };
}
