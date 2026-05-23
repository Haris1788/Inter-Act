# INTER.act — Phase 5.1 Implementation Audit

**Date:** 2026-05-23
**Branch:** `dev-sneh`
**Scope:** renderer rewrite — remove legacy Haris canvas completely;
canvas becomes Konva-only physical simulation; ReactFlow becomes
invisible pan/zoom + drag-detect plumbing.

Follows `PHASE_5_IMPLEMENTATION_AUDIT.md`. Honest tone — what was
deleted, what replaced it, what stayed intentionally bounded.

---

## 0. Executive verdict

Visual architecture inversion. Phase 5 built spatial Konva renderers
(ArmRenderer / AgvTokenLayer / AgvTrailLayer) but they sat UNDERNEATH a
legacy facade: `<WarehouseGridLayer>` painted a static blue lane +
station boxes from `/config`, `<ArmLayer>` drew arm icons,
`<ActorNode>` rendered actors as ReactFlow workflow cards, MiniMap
framed a 14×10 grid that hadn't been the source of truth since Phase 5.
Phase 5.1 deleted the facade and promoted Konva to the sole canvas
renderer. ReactFlow remains in the React tree to handle pan/zoom/drag
events, but its `<ActorNode>` is now an invisible 1×1 div — no
selection ring, no label chip, no badges, no visible pixel.

Backend semantics unchanged: this is a pure rendering correction.
Phase 5's negotiation/pathfinding/reservation invariants carry over. Two
small backend additions are observational only — `agv_path` SocketIO
event (path-preview viz) and `GET /reservations` (snapshot for live
reserved-cell rendering). Neither influences consensus.

Acceptance criterion #10 ("feels like robotic env, not workflow editor")
is the load-bearing one. After Stage 5's ambient + halo + per-kind
silhouettes, the canvas reads as a physical world — entities have body,
heading, reach, articulation; paths emerge as dashed forecasts that
shrink as AGVs traverse them; reserved cells light up with the holder's
color; empty topology = blank world, not a ghost grid.

---

## 1. Stage-by-stage delivery

| Stage | Surface | Key files |
|---|---|---|
| 1 | **Delete legacy + camera purge.** WarehouseGridLayer, ArmLayer, ZoneNode deleted. ActorNode gutted to invisible 1×1 div. MiniMap removed. New `topologyBounds(actors)` drives fitView / initial viewport — bounds derived from `TopologyActor[]` only, never from `/config` warehouse dims | `frontend/src/canvas/topologyBounds.ts` (new), `frontend/src/canvas/flow/nodes/ActorNode.tsx`, `frontend/src/canvas/overlay/KonvaOverlay.tsx`, `frontend/src/canvas/CanvasViewport.tsx`, `frontend/src/canvas/flow/useFitViewport.ts`, `frontend/src/canvas/flow/useFlowNodes.ts`, `frontend/src/canvas/flow/nodeTypes.ts` |
| 2 | **Konva-as-canonical-actor.** Position single-source from `topologySlice`; Konva layers `listening={false}` so RF pan/zoom remains; selection routes through RF `handleNodeClick` / `handlePaneClick` → `setSelectedActor`; ArmRenderer + AgvTokenLayer style by `isSelected` (reach alpha differentiation + brighter chassis stroke) | `frontend/src/canvas/overlay/ArmRenderer.tsx`, `frontend/src/canvas/overlay/AgvTokenLayer.tsx`, `frontend/src/canvas/CanvasViewport.tsx` |
| 3 | **AGV per-kind silhouettes.** New `subtype?: "shuttle" \| "forklift" \| "tow"` field on `TopologyActor` (additive, default "shuttle"); sidebar chips emit subtype via `PaletteDescriptor`; per-kind components dispatched from `AgvTokenLayer` | `frontend/src/canvas/overlay/agv/{ShuttleAgv,ForkliftAgv,TowAgv,types}.tsx` (new), `frontend/src/canvas/overlay/AgvTokenLayer.tsx`, `frontend/src/runtime/nodeFactory.ts`, `frontend/src/lib/actorNormalizer.ts`, `frontend/src/store/types.ts` |
| 4 | **Dynamic spatial overlays.** Backend: `ReservationStore.snapshot_all()` + `GET /reservations` (pure read) + `agv_path` SocketIO emit in `_traverse_to_with_path` (observational). Frontend: `DynamicGridLayer` renders dots ONLY at active cells (occupied + reserved + path); `ReservedCellsLayer` polls `/reservations` 1 Hz; `PathPreviewLayer` subscribes to `agv_path` event and renders dashed polyline ahead of AGV | `app/spatial/reservation.py`, `app/routes/replay_routes.py`, `app/agents/agv_agent.py:257`, `frontend/src/canvas/overlay/{DynamicGridLayer,ReservedCellsLayer,PathPreviewLayer}.tsx` (new), `frontend/src/runtime/socketDriver.ts`, `frontend/src/store/runtimeSlice.ts` + `types.ts`, `tests/test_snapshot_and_reservations.py` (+11) |
| 5 | **Polish + audit.** Ambient cool-dark gradient behind canvas (4-7% alpha, doesn't override snap-grid dots); reach-radius hover chip (alpha lift + `<reach>px` text near gripper); `NegotiationHaloLayer` pulses around actors in BIDDING/WON/EXECUTING state; Inspector "AGV silhouette" dropdown for AGVs | `frontend/src/canvas/CanvasViewport.tsx`, `frontend/src/canvas/overlay/ArmRenderer.tsx`, `frontend/src/canvas/overlay/NegotiationHaloLayer.tsx` (new), `frontend/src/canvas/overlay/KonvaOverlay.tsx`, `frontend/src/chrome/Inspector/SectionSpatial.tsx`, `frontend/src/runtime/socketDriver.ts`, `frontend/src/store/types.ts` |

Backend test tally: 625 pass / 5 pre-existing fail (the 5 were fixed
in `5b223e1` — Stage 4 added 11 new tests covering snapshot_all + the
new endpoint). Frontend typecheck + build clean at every stage.

---

## 2. Acceptance criteria walkthrough (from PHASE_5.1.md)

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | Old Haris warehouse patch fully gone | ✅ | `frontend/src/canvas/overlay/WarehouseGridLayer.tsx` deleted; grep shows zero live imports |
| 2 | No fixed blue lane | ✅ | Same — the blue strip lived in WarehouseGridLayer |
| 3 | No actor cards on canvas | ✅ | `frontend/src/canvas/flow/nodes/ActorNode.tsx` gutted to 1×1 invisible div; `frontend/src/canvas/flow/nodes/ZoneNode.tsx` deleted; `nodeTypes` no longer registers `zoneNode` |
| 4 | Canvas actors look physical | ✅ | `ArmRenderer` (pivot/stick/gripper/reach), per-kind AGV silhouettes (`ShuttleAgv` rounded chassis, `ForkliftAgv` body + forks, `TowAgv` body + hitch) |
| 5 | Arms visibly articulate | ✅ | Phase 5's `sweep_arm` already drove rotation via `arm_angle_update` SocketIO; ArmRenderer tweens to angle, gripper closes on PICK/PLACE (Phase 5 Stage 6) |
| 6 | AGVs visibly move like vehicles | ✅ | Phase 5's `agv_position` 10 Hz event drives per-kind silhouettes with 200 ms tween + heading + idle pulse + return arrow + return-to-origin |
| 7 | Paths are dynamic | ✅ | `PathPreviewLayer` renders A*-computed cell polyline from `agv_path` event (emitted at `agv_agent.py:257` right after `a_star` returns); `LEG_COMPLETED` for transport clears the path |
| 8 | Placement visually changes negotiation context | ✅ | Phase 5 Stage 4 acceptance gate (`test_acceptance_gate_position_dictates_winner`) proves the backend invariant; Phase 5.1 makes it visible: moving an AGV on the canvas changes the bids the user can see in the bus pane and the `PathPreviewLayer` path that emerges for the next negotiation |
| 9 | Runtime state visibly drives movement | ✅ | All canvas overlays read SocketIO + `/bus/replay` + `/reservations` + slice state. Zero hardcoded geometry |
| 10 | Feels like robotic env, not workflow editor | ✅ | Sum of #1-9 + ambient quiet-dark gradient + reach hover chip + NegotiationHaloLayer pulse around actively-in-play actors |

---

## 3. Architecture correction

**Before Phase 5.1**: ReactFlow `<ActorNode>` cards were the canonical
visual; Konva sat as decoration underneath; `/config` warehouse block
painted a fixed grid + blue lane regardless of topology; MiniMap framed
the patch as if it were the world. Users saw "workflow editor with
actor cards", not "robotic environment".

**After Phase 5.1**: ReactFlow is invisible event plumbing
(`nodesFocusable={false}`; ActorNode renders an empty 1×1 div; MiniMap
removed). Konva renders the only visible entities. Camera bounds come
from `topologyBounds(actors)` — empty world = blank canvas, NOT
ghost-grid. `/config` warehouse block is no longer consulted for camera
or render geometry (the two surviving `/config` reads in
`AgvTokenLayer` + `AgvTrailLayer` are for AGV color lookup —
informational, runtime-render only, not camera/geometry).

The decentralized negotiation invariants from Phase 1-5 are unchanged.
This is a pure rendering correction.

---

## 4. What was deleted

- `frontend/src/canvas/overlay/WarehouseGridLayer.tsx` (~223 lines) —
  the static grid + blue lane + station boxes from `/config`.
- `frontend/src/canvas/overlay/ArmLayer.tsx` — legacy arm box icon
  (Phase 5's `ArmRenderer` already superseded it functionally).
- `frontend/src/canvas/flow/nodes/ZoneNode.tsx` (~63 lines) —
  feed-I / sink-O zone markers; arm placement IS the station after
  Phase 5.
- ActorNode visual body — gutted to 1×1 invisible div (component file
  retained so RF's `nodeTypes` map still resolves).
- MiniMap mount + import in `CanvasViewport`.
- "Topology · overview" chrome label (sat next to MiniMap).
- `/config` warehouse-block reads in `StaticLayer` and inline `ArmLayer`
  inside `KonvaOverlay` — both readers removed.
- Old warehouse-grid-derived `fitView` constants in `useFitViewport`.

---

## 5. What is intentionally bounded

- **Path heatmap** (mentioned as "optional" in spec) — not implemented.
  `PathPreviewLayer` shows the CURRENT planned path; historical
  hot-cells would need to aggregate `POSITION_UPDATE` over time. Phase
  6 polish.
- **Reach-radius hover + NegotiationHaloLayer are subtle by design.**
  Spec says they must NOT outshine the entities; calibrated for ambient
  awareness, not for visual punch. Tune in Phase 6 if user feedback
  asks for it.
- **Inspector subtype dropdown is AGV-only.** Arms get reach radius;
  AGVs get subtype + reach (default 120 for AGVs even though only arms
  use reach for bid gating — kept as a renderer-visual hint).
- **`AgvTokenLayer` + `AgvTrailLayer` still read `/config` for AGV
  color lookup.** This is runtime informational metadata, not camera
  geometry. Acceptable per Phase 5.1 spec ("sidebar = metadata; canvas
  = physical entities" — color lookup serves the physical render).
  Could be folded into `topologySlice.actors[id].color` in a future
  cleanup so the canvas tree never touches `/config`.
- **Selection mirror via RF.** Spec proposed Konva-click writes;
  implementation: RF's `handleNodeClick` writes (and Konva layers stay
  `listening={false}` so all clicks reach RF). Equivalent outcome:
  single selection source in `topologySlice`'s `setSelectedActor`.
- **No new pytest coverage for the dynamic overlays.** They are pure
  visual renderers reading data sources tested elsewhere (snapshot_all
  + agv_path emit + agvPaths slice). Manual verification via browser
  loads the live state.

---

## 6. Evidence index

| Deliverable | File |
|---|---|
| `topologyBounds` camera helper | `frontend/src/canvas/topologyBounds.ts` |
| Invisible `ActorNode` | `frontend/src/canvas/flow/nodes/ActorNode.tsx` |
| Konva layer mount stack | `frontend/src/canvas/overlay/KonvaOverlay.tsx` |
| ArmRenderer (Phase 5; selection alpha + hover chip in Phase 5.1) | `frontend/src/canvas/overlay/ArmRenderer.tsx` |
| AgvTokenLayer dispatcher | `frontend/src/canvas/overlay/AgvTokenLayer.tsx` |
| Per-kind AGV silhouettes | `frontend/src/canvas/overlay/agv/{ShuttleAgv,ForkliftAgv,TowAgv}.tsx` |
| AGV `subtype` schema | `frontend/src/store/types.ts`, `frontend/src/lib/actorNormalizer.ts`, `frontend/src/runtime/nodeFactory.ts` |
| `ReservationStore.snapshot_all` | `app/spatial/reservation.py` |
| `GET /reservations` | `app/routes/replay_routes.py` |
| `agv_path` SocketIO emit | `app/agents/agv_agent.py:257` |
| `ReservedCellsLayer` (1 Hz poll) | `frontend/src/canvas/overlay/ReservedCellsLayer.tsx` |
| `PathPreviewLayer` (agv_path event) | `frontend/src/canvas/overlay/PathPreviewLayer.tsx` |
| `DynamicGridLayer` (active cells only) | `frontend/src/canvas/overlay/DynamicGridLayer.tsx` |
| `NegotiationHaloLayer` | `frontend/src/canvas/overlay/NegotiationHaloLayer.tsx` |
| Inspector AGV silhouette dropdown | `frontend/src/chrome/Inspector/SectionSpatial.tsx` |
| Backend tests for snapshot_all + /reservations | `tests/test_snapshot_and_reservations.py` |

---

*End — Phase 5.1 cutover complete. Canvas IS the simulation; ReactFlow
IS the plumbing; empty world IS a blank canvas, not a ghost grid.*
