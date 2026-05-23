# Phase 5 — Staged Implementation Plan

**Branch:** `dev-sneh` · **Spec:** `PHASE_5_SPATIAL_ORCHESTRATION_PROMPT.md` ·
**Predecessor:** Phase 4 distributed runtime (committed + verified end-to-end)
**Mode:** plan-only until approved · multi-agent parallel execution (Opus
orchestrates + reviews, Sonnet implements, Haiku UI cleanup). Every stage gets
an Opus review gate.

## Goal

Canvas becomes the source of physical truth. Backend stops assuming fixed
cells / lanes / stations / AGV bays. Negotiation outcomes become a function of
where the user dropped actors. Arms animate as articulated manipulators; AGVs
animate as vehicles that physically move and return to their dropped origin.
The system gains a real spatial engine (pathfinding + cell reservation +
collision + deadlock prevention).

Acceptance: **moving an arm/AGV on the canvas MUST change bid scores, winners,
timings, and execution paths**. If it does not, Phase 5 has failed.

## Approved decisions

- Session scope: plan only, then approve.
- E2E validation tool: `agent-browser` CLI (v0.27.0 at
  `/c/Users/snehd/AppData/Roaming/npm/agent-browser`, found locally, has its
  own skills).

## Current-state baseline (Phase 4 final)

- `pipeline.yaml` `warehouse:` block: hardcoded 14×10 grid + station
  `cell`/`dock` cells + `lane:` cells + AGV `bay:` cells.
- `pipeline_config.get_stations()` reads the warehouse block.
  `pipeline_config.get_lane_cells()` returns the lane.
  `pipeline_config.get_agvs()` returns AGV records with `bay`.
- `capability.load_profiles()`: `CapabilityProfile.position` = AGV bay cell,
  `reachable_zones` = list of station names. Arms get `reachable_zones` from
  the `served_by` mapping.
- `derive_slots(source_zone, dest_zone)` takes string zone names.
- `ArmAgent._bid_score(slot)`: returns `0.0` if `slot.zone` in
  `reachable_zones`, else `None`. NO spatial cost.
- `AGVAgent._bid_score(slot)`: Manhattan distance from current position to the
  station's dock cell.
- `AGVAgent._execute_leg`: 12-step sequence with `_straight_path` route
  (col-then-row) over `lane_cells`. Lock keys: `lane`, `station:input_arm`,
  `station:output_arm`.
- Frontend: ReactFlow actor cards on `CanvasViewport`; Konva overlay
  `WarehouseGridLayer` + `AgvTokenLayer` reads `/config` for static grid.
  Actor positions are authored but NOT used by backend (audit known-issue).

What this means for Phase 5: every spatial assumption in the backend is
hardcoded to the `warehouse:` block. The frontend has authored positions but
they are decorative. Phase 5 makes those positions load-bearing.

---

# Stage order

Stages 1→9 are sequential by dependency. Within each stage, parallelize where
the prompt marks it safe; NEVER parallelize path reservation, collision
semantics, topology authority, deadlock prevention, or spatial consensus.

## Stage 1 — Spatial topology model  [single track, foundational]

**Files:** `app/models/pipeline_config.py`, `app/agents/capability.py`,
`app/agents/negotiation.py`, new `app/models/topology.py`, tests.

Design:
- New `Topology` dataclass: `{actors: [{id, kind, x, y, reach_px, capacity,
  speed}], canvas: {width, height, cell_px}}`. Canvas units = pixels; backend
  snaps to a coarse grid (default `cell_px=40`) for pathfinding + collision.
- `pipeline_config` keeps `warehouse:` block for backward-compat tests but
  also reads a NEW `topology:` block when present. `topology:` overrides
  `warehouse:` at runtime. Add `pipeline_config.get_topology() -> Topology`.
- `CapabilityProfile` gains: `position: (x, y)` in pixels, `reach_px: float`,
  `kind` already there. Arms now have `reach_px` (default 120 = 3 cells); a
  pickup/place slot for arm A is valid only if the AGV dock position lies
  within `reach_px` of arm A's position.
- `derive_slots(source_arm_id, dest_arm_id)` now takes ARM IDs, not zone
  names. Returns 3 slots: `pickup` (kind=arm, served_by=source_arm_id,
  target_pos=arm's reach docking point), `transport` (kind=agv,
  from_pos=source dock, to_pos=dest dock), `place` (kind=arm,
  served_by=dest_arm_id, target_pos=dest dock).
- KEEP `derive_slots_by_zone` wrapper that maps current
  `input_arm`/`output_arm` strings to arm IDs via topology — preserves Phase
  3/4 zone-based tests.

**Decision fork (per stage):** topology vs warehouse precedence — proposed
**topology overrides warehouse** when both present; warehouse-only mode stays
the test fixture default.

## Stage 2 — Drag-and-drop orchestration canvas  [parallel — pure frontend]

**Files:** `frontend/src/canvas/...`, new spatial actor renderer, no backend.

Design:
- ReactFlow already supports actor drag (Phase 3). Phase 5 promotes the
  authored position from decorative to authoritative.
- Sidebar: arms + AGVs as draggable chips (already exists).
- Drop creates a Zustand `topologySlice` entry with `{id, kind, x, y,
  reach_px, capacity, speed}`. ID auto-generated.
- Optional grid snapping toggle (Shift to disable).
- Selecting an actor: Inspector shows `(x, y)`, reach radius slider, capacity.
- Delete via Inspector or Del key.
- Topology persists to localStorage (already exists for Phase 3 authored
  topology — extend schema).
- "Build & Deploy" pushes the Topology JSON to backend `/deploy/apply` (new
  payload shape adds `topology` alongside existing `arms`/`assets`).

Parallel sub-tracks within Stage 2:
- A: drag-drop wiring + Zustand schema (single track).
- B ‖ C ‖ D (after A's schema lands):
  - B: Inspector spatial fields (reach slider, position display).
  - C: Grid snapping + visual snap indicator.
  - D: Deploy payload serializer.

## Stage 3 — Dynamic actor placement runtime  [single track]

**Files:** `app/services/deploy_bridge.py`,
`app/services/container_manager.py`, `app/agents/capability.py`,
`scripts/actor_runner.py`.

Design:
- `/deploy/apply` now writes the `topology:` block into `pipeline.yaml` from
  the request body (in addition to existing arms/assets).
- `pipeline_config.reload()` refreshes the topology.
- `reconcile_actors(build_desired_from_topology())` extends the existing
  reconciler to take an actor list straight from topology (one container per
  topology actor, position passed via env). Backward-compat: when no
  topology, falls back to current arms/AGVs source.
- `actor_runner.py` loads `CapabilityProfile` from topology entry by
  `ACTOR_ID` instead of hardcoded `arms:`/`agvs:` reading. Idempotent — if no
  topology, falls back to legacy load.

## Stage 4 — Spatial negotiation + distance-aware bidding  [single track — consensus invariant]

**Files:** `app/agents/arm_agent.py`, `app/agents/agv_agent.py`,
`app/agents/negotiation.py`, tests.

Design:
- `ArmAgent._bid_score(slot)`: still pure-function. Returns `None` if slot is
  not an arm slot OR if `slot.target_pos` is outside `self.profile.reach_px`
  of `self.profile.position`. Otherwise returns the Euclidean distance from
  the arm's position to the target_pos (closer arm wins ties).
- `AGVAgent._bid_score(slot)`: returns `None` if not transport. Otherwise
  returns the Euclidean distance from `self.profile.position` to
  `slot.from_pos` PLUS the trip cost `from_pos → to_pos`. For constraint
  "longest time" the bid is negated (or `public_winner` is configured to pick
  max). For "shortest distance" (current) Manhattan stays valid via
  `derive_slots` choice.
- `public_winner` already takes `constraint`. Extend supported set to
  `shortest time`, `longest time`, `shortest distance`, `longest distance`,
  `heaviest load` (capacity-aware), and default `nearest`. Add a unit test
  per constraint asserting different placements produce different winners.

**This is the acceptance gate** for the "placement MUST change outcomes"
rule. Stage 4 test: place agv-1 at (100, 100) and agv-2 at (1000, 1000);
assert agv-1 wins under `shortest time`; assert agv-2 wins under `longest
time`; move agv-1 to (1500, 1500); assert agv-2 now wins under shortest time.

## Stage 5 — Realtime AGV movement + return-to-origin  [single track]

**Files:** `app/agents/agv_agent.py`, frontend `AgvTokenLayer`, SocketIO
events.

Design:
- AGV's dropped position from topology = its bay (return-to-origin target).
- `_execute_leg` no longer follows pre-baked `lane_cells`. Instead, computes
  a path from current pos → pickup dock via the Stage 7 spatial engine (until
  Stage 7 lands, Stage 5 uses straight-line motion as a placeholder
  documented inline).
- Movement is interpolated: AGV publishes `agv_position` SocketIO event every
  ~100 ms with `{actor_id, x, y, theta}` during traversal. Frontend
  `AgvTokenLayer` eases between updates.
- After place leg completes, AGV moves back to its dropped origin and marks
  idle. Verifiable via `agv_state.get(agv_id)["cell"] == bay` cell (in pixel
  terms: `position == initial`).

Parallel sub-tracks within Stage 5:
- A: backend movement loop + SocketIO event (single track — execution
  semantics).
- B ‖ C (after A):
  - B: frontend velocity interpolation, AGV vehicle SVG.
  - C: return-to-origin animation polish.

## Stage 6 — Arm articulation + pickup/place animation  [parallel — pure visual]

**Files:** frontend `ArmRenderer` (new), `actuation.py` already has
`sweep_arm` (just emits `arm_angle_update`).

Design:
- Arm visual = pivot point at actor position + rotating "stick" of length
  `reach_px`, with a clamp/gripper at the tip. SVG or Konva primitives.
- Angles emitted by backend `sweep_arm` already drive the visual via
  `arm_angle_update`. Phase 5 adds: visible reach radius circle, idle
  breathing animation, pickup gesture (grip close).
- Resting state: stick vertical, gripper open. Pickup: rotate to slot, grip
  close. Place: rotate to deposit, grip open.

Parallel sub-tracks (3 concurrent):
- A: ArmRenderer SVG + rotation primitive.
- B: reach radius visualization.
- C: pickup/place gesture polish + idle breathing.

## Stage 7 — Pathfinding + collision + reservation system  [single track — consistency surface]

**Files:** new `app/spatial/pathfinder.py`, `app/spatial/reservation.py`,
extend `app/models/grid_state.py`, tests.

Design:
- Grid: derived from canvas dims + `cell_px`. Cells holding actors
  (arm pivot ± reach) marked obstacle.
- Pathfinder: A* with 4-connected neighbours, Manhattan heuristic. Returns
  list of cells from `start` to `goal`. Recomputed per leg per AGV.
- Reservation: AGV calls `reservation.reserve(task_id, actor_id, path_epoch,
  cells, ttl_ms)` before traversing. Each cell becomes a Redis key keyed by
  the **full tuple** `resv:task:<task_id>:actor:<actor_id>:epoch:<path_epoch>:cell:<col>:<row>`
  with TTL. `path_epoch` is a monotonic counter per `(task_id, actor_id)`
  incremented on every replan. **NEVER use a bare `cell:<col>:<row>` key** —
  that loses ownership context and creates ghost occupancy after replans
  (see refinement note below). Conflicts → A* replans avoiding cells held by
  ANY non-self epoch (and bumps own epoch).
- Collision: pre-existing `grid_state.set_cell` already enforces "one AGV per
  cell". Reservation extends to a slack window of N cells ahead.
- Deadlock prevention: total order on cells (lex by `(col, row)`). When two
  AGVs need overlapping cells, lower-id AGV gets it (deterministic ordering;
  no banker's algorithm in scope). Refinement: timeout-and-replan after T.
- Reservation TTL renewal via the existing `LockKeepalive` pattern from
  Phase 4 Stage 2.

Parallel sub-tracks within Stage 7:
- A (single track): pathfinder + reservation primitives + tests.
- B (after A): integrate into `AGVAgent._execute_leg` replacing
  straight-line motion.

## Stage 8 — Spatial replay + observability  [parallel — independent surfaces]

**Files:** new `frontend/src/chrome/Spatial/Trail.tsx`,
`PositionHistory.tsx`, backend extends `/bus/replay` to include
`agv_position` snapshots, observability docs.

Design:
- Backend records `agv_position` snapshots into the bus stream as new
  observational `MessageType.POSITION_UPDATE` (16th type) at coarse intervals
  (every 500 ms) for replay fidelity.
- Frontend Replay panel adds a "trail" overlay showing AGV path traversed
  per task. Scrubbing the cursor moves AGV tokens to their position at that
  time.
- ActorInspector shows the live `position` + `target` + `reach`.

Parallel sub-tracks (3 concurrent):
- A: backend POSITION_UPDATE emission + replay endpoint extension.
- B: frontend Trail overlay rendering.
- C: scrub-to-position playback wiring.

## Stage 9 — Chaos + spatial integration testing + E2E via agent-browser  [single + parallel within]

**Files:** new `tests/distributed/test_spatial_chaos.py`,
`tests/e2e/agent_browser_scenarios/` (shell scripts driving
agent-browser CLI), `tasks/phase5_e2e_runbook.md`.

Design:
- Spatial chaos tests (parametrized): place actors at varied positions;
  assert winner changes; inject AGV crash mid-traverse and verify path
  replan + reservation release.
- Mandatory 4 E2E scenarios via `agent-browser` CLI (Vercel) per Phase 5
  prompt's mandate:
  1. Basic transport negotiation (place arms + 3 AGVs at varied distances;
     enter "move 1 parcel using shortest time"; nearest AGV wins; full
     execution + return-to-origin).
  2. Constraint-sensitive (longest time wins farthest AGV).
  3. Multi-agent coordination (3 objects, 2 AGVs).
  4. Fault recovery (kill an AGV mid-execution; rebid + winner switch).
- For each scenario: capture bus log, screenshots, position deltas, prove
  decentralized + leaderless + placement-driven.

E2E scripting note: `agent-browser skills get core --full` exposes selector
patterns and refs; we'll write one shell script per scenario invoking the CLI
with selector-based steps + assertion of bus messages via curl.

Parallel sub-tracks within Stage 9:
- A (single track): chaos harness + 3 spatial chaos tests.
- B ‖ C ‖ D ‖ E (4 concurrent E2E scenarios after the harness lands).

---

# Decisions needing your sign-off (per stage)

1. **Stage 1** — coordinate units: **canvas pixels with backend coarse-grid
   snap** (cell_px=40 default) vs pure-cell vs pure-px. Proposed: hybrid (px
   on canvas, cells in backend pathfinder/reservation). Rationale: canvas
   needs free placement; pathfinder + locks need discrete cells.
2. **Stage 1** — `warehouse:` vs `topology:` precedence: **topology
   overrides** when both present. Warehouse stays the test fixture default
   so existing 520 tests keep passing without modification.
3. **Stage 4** — bid metric: **Euclidean distance** for time-based
   constraints, Manhattan for distance-based. `public_winner` extended with
   `longest time` / `longest distance` / `heaviest load`. Proposed.
4. **Stage 5** — return-to-origin: **always** after leg completion. AGV's
   dropped position = persistent bay. Proposed.
5. **Stage 5** — position update cadence: **100 ms** (10 Hz) for live
   `agv_position`, 500 ms for `POSITION_UPDATE` on the bus log (replay).
   Proposed.
6. **Stage 6** — arm visual primitive: **SVG inside Konva layer** (existing
   Konva tooling). Alternative: pure ReactFlow node. Proposed SVG-in-Konva
   for parity with existing AGV token rendering.
7. **Stage 7** — pathfinder: **A* on a 4-connected grid** derived from
   canvas + cell_px. Manhattan heuristic. Alternative: D* Lite (incremental
   replan) or JPS. Proposed A* — simplest honest fit for grid size; replan
   per leg is cheap.
8. **Stage 7** — reservation primitive: **per-cell Redis SET with TTL +
   `LockKeepalive`** during traversal, keyed by the full tuple
   `(task_id, actor_id, path_epoch, cell_id)`. `path_epoch` is a monotonic
   counter bumped on every replan. Conflict → A* replans avoiding non-self
   epoch cells AND bumps own epoch. Cleanup garbage-collects old-epoch keys
   via SCAN-by-prefix `resv:task:T:actor:A:epoch:<E:` on epoch increment —
   no per-cell tracking by caller. Proposed.

   **Rationale (user refinement):** without epoch scoping, stale
   reservations after replans/recovery become indistinguishable from current
   ownership and create "ghost occupancy" bugs that are very hard to debug
   once replay (Stage 8) is showing historical state. Epoch makes ownership
   monotonic + replay diff-able + cleanup idempotent. This is the right
   architectural invariant to bake in before Stage 7 implementation, not as
   a Stage 8 hotfix.
9. **Stage 7** — deadlock prevention: **deterministic cell-order tie-break
   (lex by col, row) + timeout-replan after T**. No banker's algorithm.
   Proposed.
10. **Stage 8** — `POSITION_UPDATE` MessageType added as a 16th observational
    type (mirrors LEG_STARTED pattern). Proposed.

# Constraints (from Phase 5 spec)

- No central planner. No central route ownership. No hidden coordinators. No
  orchestration shortcuts.
- Interpreter parses goals. Actors own decisions. Bus owns shared reality.
  Consensus emerges.
- Physical/runtime truth, NOT fake robotics theater, NOT hardcoded movement,
  NOT visual-only simulation.

# Deferred (per Phase 5 spec)

advanced robot physics, SLAM, ROS, RL, computer vision autonomy, swarm
optimization.

# Parallelization summary

| Stage | Single-track surface | Parallelizable |
|---|---|---|
| 1 | Topology model + capability shape | none (foundation) |
| 2 | Zustand schema + drag-drop wiring (A) | Inspector + snapping + serializer (B/C/D) |
| 3 | Deploy bridge + reconciler topology source | none (consistency) |
| 4 | Bid metric + public_winner extension | none (consensus invariant) |
| 5 | Movement loop + position event (A) | Frontend velocity tween + return polish (B/C) |
| 6 | none | 3 concurrent visual tracks |
| 7 | Pathfinder + reservation (A) | Integration (B) |
| 8 | none | 3 concurrent obs tracks |
| 9 | Chaos harness (A) | 4 concurrent E2E scenarios (B/C/D/E) via agent-browser |

# Risk register

- **Topology hot-swap mid-negotiation** — deploying a new topology while a
  task is in-flight could invalidate participant state. Mitigation: deploy
  blocked when any task is active; bus log explicitly carries the topology
  version per round.
- **Pathfinder replan cost** — A* per leg per AGV is cheap at canvas scale.
  Bound: 50×50 cell grid; A* worst case ~2500 nodes; replan time well under
  10 ms.
- **Reservation deadlock under heavy contention** — total order + timeout
  cover the common cases; truly concurrent N≥3 AGV deadlock is documented as
  a known Phase 6 escalation.
- **Visual frame budget** — 10 Hz AGV position + 5 Hz arm angle updates with
  N=10 actors = 150 events/s. Konva handles this; Redis SocketIO mirror
  needs to keep up. Mitigate with batched emit if observed degraded.
- **Phase 5 acceptance test gate** — Stage 4 test (place changes winner) is
  the load-bearing acceptance. If it does not pass, the rest of Stage 5–9 is
  built on a non-working spatial bid. Order: Stage 4 first cannot be skipped.

# Acceptance criteria (Phase 5 prompt rule)

Moving an arm/AGV on the canvas MUST change:
- bids (visible in `bus.history()`),
- winners (different `accept` sender for the same task),
- timings (leg duration scales with distance),
- runtime execution (AGV path differs).

If ANY of those is invariant under canvas position changes, Phase 5 has
FAILED. The Stage 4 spatial-bid unit test + Stage 9 E2E Scenario 1
explicitly assert this.
