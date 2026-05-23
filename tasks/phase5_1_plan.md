# Phase 5.1 — Staged Implementation Plan

**Branch:** `dev-sneh` · **Spec:** `PHASE_5.1.md` (renderer rewrite, remove
legacy Haris canvas completely) · **Predecessor:** Phase 5 spatial runtime
committed + verified.

**Mode:** plan-only until approved. Multi-agent parallel execution where
the prompt marks it safe; never parallelize anything that touches the
single "what is the canvas" decision.

## Goal

Inversion. Backend went spatial in Phase 5; the frontend still mounts
ReactFlow `<ActorNode>` cards as the canonical canvas visual with Konva
layers underneath. Phase 5.1 promotes Konva to the sole renderer of
physical entities, makes RF invisible plumbing (pan/zoom + drag-detect),
DELETES the static Haris warehouse renderer (grid patch, blue transport
lane, square station boxes), and adds a per-kind AGV silhouette set +
dynamic spatial overlays driven by live runtime state.

If after this phase the canvas still feels like a node editor, a
dashboard, or a static mockup, **the phase failed**.

## Approved decisions

- Session scope: plan only, then approve.
- ReactFlow fate: **invisible pan/zoom + drag-detect**, delete `ActorNode`
  visuals. Existing `topologySlice`/`actorNormalizer` wiring preserved.

## Current-state baseline (post-Phase 5)

- `frontend/src/canvas/CanvasViewport.tsx` — mounts ReactFlow + Konva
  overlay; RF nodes are `actorNode` (ArmCard/AGVCard visual) + `zoneNode`.
- `frontend/src/canvas/flow/nodes/ActorNode.tsx` — RF node renders actors
  as workflow cards with selection rings, label chips, status pills.
- `frontend/src/canvas/overlay/WarehouseGridLayer.tsx` — STATIC grid +
  stations + lane painted from `/config` (`warehouse:` block).
- `frontend/src/canvas/overlay/ArmLayer.tsx` — legacy arm box / icon
  rendering (Phase 1-era; survives unused after Phase 5's `ArmRenderer`).
- `frontend/src/canvas/overlay/ArmRenderer.tsx` (Phase 5) — articulated
  pivot/stick/gripper. Live + correct. Mounted between `ArmLayer` and
  `AgvTokenLayer` in `KonvaOverlay`.
- `frontend/src/canvas/overlay/AgvTokenLayer.tsx` (Phase 5) — vehicle
  rounded-rect body + heading triangle + tween + idle pulse + return
  arrow + status dot.
- `frontend/src/canvas/overlay/AgvTrailLayer.tsx` (Phase 5) — polyline
  trails from `POSITION_UPDATE` history.
- `frontend/src/canvas/overlay/KonvaOverlay.tsx` — layer mounting.
- `frontend/src/runtime/socketDriver.ts` — `bus_message` /
  `agv_position` / `arm_angle_update` handlers.

Phase 5 built the right pieces UNDER a wrong-looking facade. Phase 5.1
removes the facade.

---

# Stage order

Stages run sequentially because the deletions in S1 unblock the
"canvas-is-Konva" assumption every later stage relies on.

## Stage 1 — Delete legacy renderer  [single track, foundational]

**Files to delete or gut:**
- `frontend/src/canvas/overlay/WarehouseGridLayer.tsx` — DELETE. Static
  blue lane + station boxes + grid go.
- `frontend/src/canvas/overlay/ArmLayer.tsx` — DELETE (Phase 5's
  `ArmRenderer` is the canonical arm visual).
- `frontend/src/canvas/flow/nodes/ActorNode.tsx` — GUT to a transparent
  div with the same RF node footprint (so RF still tracks the node for
  pan/zoom/drag math) but renders NOTHING visible. Optionally a 1×1
  invisible hit area for selection-on-click. Selection ring + label chip
  + status pill all gone.
- `frontend/src/canvas/flow/nodes/ZoneNode.tsx` (if present) — same
  treatment OR delete if no longer needed (zones become dynamic overlay
  in S4).
- `frontend/src/canvas/overlay/KonvaOverlay.tsx` — remove
  WarehouseGridLayer/ArmLayer imports + mounts.
- `/config` warehouse-block consumption — drop frontend fetches that
  paint the static patch (any `useConfig` / fetch in the overlay tree).

**Also kill camera-level Haris remnants (user refinement):**

The Haris patch survives invisibly in viewport behavior unless explicitly
purged. `fitView()` reads node bounding boxes; minimap reads `/config`
dimensions; initial centering assumes warehouse grid. After visual delete,
the camera would still center+frame on 14×10 cells of empty space.

- `CanvasViewport.tsx` initial viewport / `fitView()` math — derive
  bounds from `TopologyActor[]` only:
  ```ts
  bounds = topologyActors.length === 0
    ? { x: 0, y: 0, width: 800, height: 600 }   // sensible empty default
    : unionOf(actors.map(a => bbox(a.x, a.y, a.reach_px ?? 40)))
        .padded(160);
  ```
- MiniMap (`<MiniMap>` in `CanvasViewport.tsx`): drop entirely OR
  rebuild viewBox from `TopologyActor[]` bounds. **Proposed: drop.**
  Per Phase 5.1 spec the canvas IS the simulation; a workflow-editor
  minimap is exactly the kind of thing being deleted.
- Any `useFitViewport()` / `defaultViewport` constants tied to warehouse
  grid → replace with topology-bounds calc.
- Any `/config` reads in the canvas tree that pull warehouse dims for
  camera math → delete.

**Verify:**
- `npm run typecheck && npm run build` clean (defunct imports removed).
- Visual: stack loads; canvas shows ArmRenderer + AgvTokenLayer +
  AgvTrailLayer only; no blue lane, no station boxes, no actor cards.
- **Empty topology** (no actors placed) loads to a sensible empty view
  centered at origin — NOT centered on the ghost of a 14×10 grid.
- Place one actor → fitView frames on actor bounds, not warehouse bounds.

**Opus review gate:** no hidden re-mount of deleted layers; selection +
drag still work through RF (now invisible); **camera bounds derive ONLY
from `topologySlice` actor list, never from `/config` warehouse dims**.

## Stage 2 — Konva-as-canonical-actor  [single track]

**Files:**
- `frontend/src/canvas/CanvasViewport.tsx` — RF nodes become transparent
  shells. RF's `nodes` array is still the source of truth for position,
  drag math, snap, viewport transform. Konva layers read positions from
  the same `topologySlice` and render the physical entities at the
  matching screen coords (RF→screen transform applied to Konva stage).
- `frontend/src/canvas/overlay/ArmRenderer.tsx` — confirm position seam
  reads `topologySlice` actor `(x, y)`, not the old RF node position.
  Should already; verify + add reach-radius hit area for selection.
- `frontend/src/canvas/overlay/AgvTokenLayer.tsx` — same: position from
  slice + runtime override from `agv_position`. Add selection hit area
  around body.
- Selection: clicking a Konva entity dispatches to the existing
  selection slice. RF's selection becomes a side-effect / mirror, not the
  source of truth.

**Verify:**
- Drag an arm: Konva pivot follows in real time (RF emits position
  changes, slice updates, Konva re-renders).
- Click an AGV vehicle: Inspector shows that AGV (selection wired
  through).
- Snap toggle still works (RF `snapGrid` prop unchanged).
- Inspector edits (reach slider) update the Konva reach circle live.

**Opus review gate:** position seam is single-source — `topologySlice`
`(x, y)` — never reads from RF node directly. No "double truth" between
RF and Konva.

## Stage 3 — AGV per-kind silhouettes  [parallel — 3 visual subtypes]

Spec mandates Shuttle / Forklift / Tow visual subtypes.

**Schema change:**
- `TopologyActor` gets optional `subtype?: "shuttle" | "forklift" | "tow"`
  for AGVs. Default = "shuttle" (current rounded-rect = shuttle silhouette).
- Sidebar drag chips already segment by kind. Phase 5.1 splits the
  Mobile Agents section: each chip carries a `subtype`. Existing chips
  ("Shuttle AGV / Forklift AGV / Tow AGV") provide the subtype hint.
- localStorage migration: add `subtype: "shuttle"` to old AGV entries
  on load.

**Visual subtypes** (sibling components under
`frontend/src/canvas/overlay/agv/`):
- `ShuttleAgv.tsx` — low-profile rounded-rect (current shape; promote
  here).
- `ForkliftAgv.tsx` — taller upright body + visible forks (two parallel
  prongs in front).
- `TowAgv.tsx` — compact body + a short towing hitch behind.

`AgvTokenLayer.tsx` dispatches by `subtype` to the right component.
Common props: `(x, y, theta, color, carried, status)`.

**Parallel tracks:** 3 subtype renderers can be built concurrently after
the schema + dispatcher land.

**Verify:** drag a Forklift chip → forklift silhouette appears, not
shuttle. Subtype persists across reload.

## Stage 4 — Dynamic spatial overlays  [single track, runtime-driven]

Spec: live grid, reserved cells, negotiated paths, AGV trails (already
in Phase 5), route previews, optional heatmap.

**New Konva layers:**
- `ReservedCellsLayer.tsx` — reads `/bus/reservations` (new lightweight
  read endpoint OR derived from `POSITION_UPDATE` + Stage 7 reservation
  store snapshot). Renders the cells currently reserved per actor with
  the actor's color at 25% alpha. Updates on a 1 Hz poll OR via SocketIO
  if a `reservation_update` event is added (optional — poll suffices for
  Phase 5.1).
- `PathPreviewLayer.tsx` — for an AGV currently executing transport,
  renders its A*-computed path as a faint dashed polyline ahead of the
  current position. Source: a new `agv_path` SocketIO event emitted by
  the AGVAgent right after `a_star(...)` returns and before traversal
  begins. Payload `{actor_id, task_id, path_cells, epoch}`.
- `DynamicGridLayer.tsx` — replaces the deleted WarehouseGridLayer with
  a MUCH subtler grid: faint dots ONLY at cells that have current
  activity (occupied, reserved, on a recent path). No always-on grid
  paint. The "negotiation environment" emerges from runtime, not from a
  hardcoded background.

**Backend small addition:**
- `app/agents/agv_agent.py` — after `a_star(...)` in
  `_traverse_to_with_path`, emit `socketio.emit("agv_path", {...})` with
  the planned path cells. Observational, no protocol impact.
- `app/routes/replay_routes.py` — new `GET /reservations` returning a
  snapshot of `held_by_others(...)` across all tasks (or all
  reservations grouped by actor) for the frontend's 1 Hz poll. Pure read.

**Verify:** spawn a negotiation → on consensus, see a faint dashed path
appear ahead of the winning AGV → cells along the path light up with
the AGV's color → as AGV traverses, path shortens, trail extends behind.

**Opus review gate:** new overlays read ONLY from live runtime
(SocketIO events + `/bus/replay` + `/reservations`); no hardcoded
geometry. `agv_path` is observational, no consensus impact.

## Stage 5 — Acceptance polish + audit  [parallel — independent surfaces]

**Subtle:**
- Idle ambient: very faint canvas background gradient (cool dark) so the
  empty world doesn't feel like a missing render.
- Reach radius hover: hovering an arm slightly lifts its reach circle
  alpha + shows a numeric `<reach>px` chip near the gripper.
- Negotiation pulse: when an actor is BIDDING / WON / EXECUTING (state
  from `availability_update` or LEG_*), its silhouette gets a soft halo
  pulse so the viewer sees who's currently in play.
- Sidebar copy update: existing chips already say Shuttle/Forklift/Tow.
  Inspector "Spatial" section gets a `subtype` dropdown for AGVs.

**Audit:** new `PHASE_5_1_IMPLEMENTATION_AUDIT.md`. Documents what was
deleted, what visual primitives replace it, the acceptance criteria
from the prompt with PASS/FAIL evidence per item.

**Parallel tracks:**
- A: subtle visual polish (ambient + hover + halo).
- B: Inspector subtype dropdown.
- C: audit doc.

---

# Decisions needing your sign-off (per stage)

1. **Stage 1** — `ZoneNode` fate (the `feed-I` / `sink-O` zone markers):
   **delete** (Phase 5 made zones obsolete — stations come from arm
   placement). Alt: keep as draggable feed/sink markers. **Proposed:
   delete**; arm placement IS the station.
2. **Stage 2** — selection source-of-truth: **Konva click dispatches to
   `selectionSlice`**; RF selection mirrors it. Proposed.
3. **Stage 3** — AGV subtype storage: **new `subtype?` field on
   `TopologyActor`**, sidebar chips already segment by subtype, default
   `shuttle` on migration. Proposed.
4. **Stage 4** — reservation visualization source: **1 Hz poll of new
   `GET /reservations`** (cheaper than adding a SocketIO event). Proposed.
5. **Stage 4** — `agv_path` SocketIO event: **observational, payload
   `{actor_id, task_id, path_cells, epoch}`**. Proposed.
6. **Stage 4** — `DynamicGridLayer`: **subtle dots only at active cells
   (occupied + reserved + recent path)**, no always-on grid. Proposed.
   Alt: full grid at very low alpha. The "negotiation environment
   emerges from runtime" framing favours active-cells-only.
7. **Stage 5** — Inspector subtype dropdown: **add for AGVs only**.
   Proposed.

# Acceptance criteria (per spec)

Phase 5.1 PASSES only if all 10 acceptance criteria from the prompt are
met. Audit doc in Stage 5 walks each item with evidence:

1. Old Haris warehouse patch fully gone — proven by absence of
   `WarehouseGridLayer` import.
2. No fixed blue lane — same.
3. No actor cards on canvas — `ActorNode` gutted to invisible.
4. Canvas actors look physical — ArmRenderer + ShuttleAgv + ForkliftAgv
   + TowAgv silhouettes.
5. Arms visibly articulate — Phase 5's `sweep_arm` already drives
   rotation; verified in browser.
6. AGVs visibly move like vehicles — Phase 5 tween + heading;
   per-kind shape Phase 5.1.
7. Paths are dynamic — `PathPreviewLayer` shows A*-computed cells.
8. Placement changes negotiation context — Phase 5 acceptance gate
   passes; Phase 5.1 just visualizes it.
9. Runtime state drives movement — all overlays read live SocketIO +
   `/bus/replay` + `/reservations`.
10. Feels like robotic environment, not workflow editor — sum of above
    + ambient + halo + dynamic grid.

# Parallelization summary

| Stage | Single-track surface | Parallelizable |
|---|---|---|
| 1 | Deletion + import cleanup | none |
| 2 | Konva-as-truth seam | none (single position source rule) |
| 3 | Subtype schema + dispatcher | 3 concurrent subtype renderers |
| 4 | New backend event + endpoint + DynamicGridLayer | reservation + path overlays after foundation |
| 5 | none | 3 concurrent polish tracks (ambient + Inspector dropdown + audit) |

# Risk register

- **Konva ↔ RF transform sync.** RF applies its own pan/zoom transform
  to nodes; Konva needs the same transform on its Stage to keep entities
  aligned. Phase 5's `KonvaOverlay` already does this via `tx`; verify
  unchanged after Stage 2.
- **Selection ambiguity.** If RF still surfaces a node-selected event,
  it could fight Konva's. Stage 2's "Konva owns selection" rule
  resolves; tests/checks must prove RF doesn't auto-select on drag-end.
- **Subtype migration drift.** Old AGV entries without `subtype` must
  render as shuttle, not crash. Migration via `actorNormalizer` `??`
  fallback.
- **`/reservations` cost.** SCAN-by-prefix on every poll across all
  tasks. At Phase 5 scale (few tasks, few cells) trivial. Document the
  budget; revisit if scale grows.

# What this is NOT

- Not a styling pass. Real renderer rewrite.
- Not new runtime functionality. Phase 5.1 doesn't change backend
  consensus or pathfinding semantics beyond two observational additions
  (`agv_path` event, `/reservations` endpoint).
- Not a switch to Pixi/Three.js. Konva is enough at this scale.

# After approval

I expand Stage 1 committed sub-spec (per Phase 5 pattern's `stage1_spec`
files) and dispatch the deletion agent. Each subsequent stage gets its
own sub-spec on stage entry.
