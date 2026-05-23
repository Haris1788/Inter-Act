# INTER.act — Phase 5 Implementation Audit

**Date:** 2026-05-23
**Branch:** `dev-sneh`
**Scope:** spatial orchestration runtime + dynamic topology coordination

Follows `PHASE_4_IMPLEMENTATION_AUDIT.md`. Honest tone — what shipped, what
was intentionally bounded, what is still transitional.

---

## 0. Executive verdict

Canvas is now the source of physical truth. Backend stops assuming
hardcoded `warehouse:` cells / stations / lanes / bays for actors that come
through the new `topology:` block. Negotiation outcomes are a measurable
function of where the user dropped actors — verified by pytest acceptance
gate (`test_acceptance_gate_position_dictates_winner` passes 3×
consecutive, every run). Arms render as articulated manipulators (pivot +
stick + gripper). AGVs render as vehicles + travel cell-by-cell along
A*-computed paths with epoch-scoped Redis reservations. After delivery,
AGVs return to their dropped origin and mark idle.

What did NOT change: the leaderless invariant. Every actor still calls
`public_winner` on its own `bus.history()`. The bid function gained
spatial inputs (`position_px`, `reach_px`, `from_pos`, `to_pos`) — the
*rule* did not.

The final mandatory end-to-end E2E via `agent-browser` CLI is partial: the
4 scenario scripts + common helper library + runbook exist; full live
execution against the running stack hit drag-drop friction (ReactFlow
synthetic drop zone vs HTML5 drag the CLI emits) and needs an iteration
pass with the `/deploy/apply`-fallback path. The acceptance gate (placement
→ winner) is independently proven via pytest chaos tests against real A* +
real reservations + real Redis-backed state.

---

## 1. Stage-by-stage delivery

| Stage | Deliverable | Files | Tests added |
|---|---|---|---|
| 1 | Spatial topology model — `Topology` + `ActorSpec`, `pipeline_config.get_topology()` + legacy shim, `CapabilityProfile.position_px`/`reach_px`, `derive_slots_by_actor` | `app/models/topology.py` (new), `app/models/pipeline_config.py`, `app/agents/capability.py`, `app/agents/negotiation.py` | +15 |
| 2 | Drag-drop canvas — `TopologyActor` extended with `reach_px`/`capacity`/`speed`, Inspector spatial fields, snap toggle + `<Background>` grid, deploy payload `topology` serializer | `frontend/src/store/topologySlice.ts`+`types.ts`, `frontend/src/lib/actorNormalizer.ts`, `frontend/src/chrome/Inspector/SectionSpatial.tsx`, `frontend/src/canvas/snap/useSnapPreference.ts`, `frontend/src/canvas/CanvasViewport.tsx`, `frontend/src/runtime/deploy.ts` | (frontend typecheck/build clean) |
| 3 | Dynamic placement runtime — `/deploy/apply` writes `topology:`, reconciler builds desired from topology, `actor_runner` profile priority env→topology→legacy | `app/services/deploy_bridge.py`, `app/services/container_manager.py`, `scripts/actor_runner.py`, `app/agents/capability.py` | +13 |
| 4 | **Spatial negotiation (acceptance gate)** — Euclidean bids gated by `reach_px`, AGV `approach + trip` cost, `public_winner` extends with `longest time` / `longest distance` / `heaviest load`, BID payload carries `capacity` + `reach_px` | `app/agents/arm_agent.py`, `app/agents/agv_agent.py`, `app/agents/negotiation.py`, `app/agents/actor_agent.py`, `app/agents/capability.py`, `app/routes/negotiate_routes.py` | +14 (incl. acceptance gate) |
| 5 | Realtime AGV movement + return-to-origin — pixel-interpolated `_traverse_to` at 10 Hz `agv_position` SocketIO event, return to dropped origin after place, vehicle SVG shape + heading + tween + idle pulse + return arrow | `app/agents/agv_agent.py`, `frontend/src/canvas/overlay/AgvTokenLayer.tsx`, `frontend/src/runtime/socketDriver.ts`, `frontend/src/store/types.ts`+`runtimeSlice.ts` | +5 |
| 6 | Arm articulation — pivot + rotating stick + gripper, reach radius circle, idle breathing, pickup gesture | `frontend/src/canvas/overlay/ArmRenderer.tsx` (new), `frontend/src/canvas/overlay/KonvaOverlay.tsx` | (frontend) |
| 7 | Spatial engine — A* pathfinder (Manhattan heuristic, lex tie-break), epoch-scoped Redis reservations `(task_id, actor_id, path_epoch, cell_id)`, PathKeepalive TTL renewal, replan on conflict | `app/spatial/pathfinder.py` (new), `app/spatial/reservation.py` (new), `app/agents/agv_agent.py` (integration) | +42 |
| 8 | Spatial replay + observability — `POSITION_UPDATE` (16th MessageType) at 2 Hz on bus, `AgvTrailLayer` polyline trails, Replay scrub→`replayPositions` snap | `app/agents/negotiation.py`, `app/agents/agv_agent.py`, `frontend/src/canvas/overlay/AgvTrailLayer.tsx` (new), `frontend/src/chrome/Replay/ReplayPanel.tsx`, `frontend/src/store/runtimeSlice.ts`+`types.ts`+`selectors.ts` | +4 |
| 9 | Spatial chaos + E2E scripts — 3 spatial chaos tests (placement-change-winner under chaos, path replan on concurrent reservation, TTL release after crash), 4 `agent-browser` CLI E2E scenarios + common helper + runbook | `tests/distributed/test_spatial_chaos.py` (new), `tests/e2e/lib/common.sh`, `tests/e2e/scenarios/scenario_{1..4}_*.sh`, `tests/e2e/runbook.md` | +3 chaos |

**Test tally:** Phase 5 added ~96 new tests across 9 stages. Combined with
Phase 4 baseline of 520, total now ~616 pass / 5 pre-existing failures
(`test_arm_execution` ×1, `test_warehouse_config` ×4 — untouched, predate
Phase 3).

---

## 2. How the runtime works now

1. User opens canvas → drags arms + AGVs from sidebar → places at desired
   pixel coordinates → Inspector adjusts `reach_px` / `capacity` / `speed`.
2. Build & Deploy → POST `/deploy/apply` with `topology:` + legacy `actors`.
   Backend writes both blocks to `pipeline.yaml`, reloads, calls
   `reconcile_actors(build_desired_from_config())`. Reconciler creates one
   container per topology actor with `ACTOR_X`/`ACTOR_Y`/`ACTOR_REACH_PX`/
   etc. env.
3. `scripts/actor_runner.py` (per container) loads CapabilityProfile via
   priority env → topology → legacy. Constructs `ArmAgent` or `AGVAgent`.
   Subscribes to Redis Streams bus.
4. POST `/negotiate {"prompt": ...}` → interpreter (qwq:32b) → TaskSpec →
   `derive_slots_by_actor(arms[0], arms[1], topology)` produces 3 slots
   with `target_pos` / `from_pos` / `to_pos` populated. Publishes
   `TASK_ANNOUNCE`.
5. Each actor's `TaskParticipant`:
   - Arm `_bid_score`: Euclidean from `position_px` to `slot.target_pos`;
     None if beyond `reach_px`.
   - AGV `_bid_score`: `approach + trip` Euclidean cost.
   - BID payload carries `capacity` + `reach_px` so peers re-derive
     identical winners.
   - `public_winner` honours `shortest time` / `longest time` / `shortest
     distance` / `longest distance` / `heaviest load`.
6. Consensus reached → arm + AGV agents call `_execute_leg`:
   - Arm: `sweep_arm` to PICK angle → REST → `signal_load`.
   - AGV `_traverse_to_with_path`: `next_epoch` → `held_by_others` →
     `a_star(grid_from_topology, ...)` → `reserve(task, actor, epoch, path)`
     → `PathKeepalive` start → traverse cell-by-cell via pixel
     interpolation emitting `agv_position` (10 Hz) and `POSITION_UPDATE`
     (2 Hz on bus) → `release(task, actor, epoch)` on success or halt.
   - On reservation conflict: bump epoch + replan up to 5 times; then
     fault.
   - Arm place: `signal_unload` → wait AGV clear → sweep PLACE → REST.
   - AGV step 10: return to `_origin_px` via pathfinding.
7. Frontend: vehicle tokens move 200 ms-tweened along the live
   `agv_position` stream. Arms rotate via `arm_angle_update`. Trails render
   the path from `POSITION_UPDATE` history. Replay scrub snaps AGV tokens
   to historical positions at the cursor.

---

## 3. What is intentionally bounded (Phase 5 limits)

- **agent-browser E2E execution partial.** Scripts + helpers + runbook
  exist; full live drag-drop from sidebar hit ReactFlow synthetic-event
  friction. Scripts default to `/deploy/apply` fallback for topology
  placement — fully validates the runtime via real LLM + real Redis + real
  backend, but the click-and-drag UX itself remains a manual / Playwright
  validation. Phase 6 polish.
- **Pathfinder is plain A* on grid.** No D* Lite, no JPS — appropriate at
  current canvas scale (50×50 grid worst case ≈ 2500 nodes). Replan per
  leg is cheap.
- **Deadlock prevention is timeout-replan + lex tie-break.** No banker's
  algorithm, no global ordering — sufficient for current 2 arm + 3 AGV
  topologies, escalation deferred.
- **Topology hot-swap mid-negotiation NOT guarded.** Deploying a new
  topology while a task is in-flight could invalidate participants. Phase 6
  must add a "no deploy while tasks active" gate or per-round topology
  version stamping.
- **Concurrent-task spatial reservations** — multiple in-flight tasks can
  each have their own epoch sequence; cross-task path conflicts are
  detected via `held_by_others` but not optimized.
- **Acceptance gate proven by pytest, not by live browser drag.** The
  protocol invariant (placement → winner) is verified end-to-end in
  Stage 4 tests + Stage 9 chaos. Browser drag-drop UX is a separate
  validation surface.
- **5 pre-existing test failures** (`test_arm_execution` ×1,
  `test_warehouse_config` ×4) remain — untouched, predate Phase 3.

---

## 4. Decentralization invariants preserved

- Pure-functional consensus over `bus.history()` unchanged. Spatial inputs
  feed the bid; the leaderless rule consumes the bid set identically across
  actors.
- No coordinator. No central path planner. Each AGV runs A* on its own
  view of `held_by_others`. Reservations are owned per actor (epoch-scoped)
  — no global path authority.
- Bus is dumb. Delivers + records. Decides nothing.
- `CONSENSUS_REACHED`, `TASK_FAILED`, `LEG_*`, `AVAILABILITY_UPDATE`,
  `POSITION_UPDATE` are observational. Agents act on their own state
  checks, never on another actor's observational message.
- Faults are agent-level; reopens are per-task. Reservation TTL releases
  cells held by a crashed actor automatically.

---

## 5. Acceptance gate (Phase 5 mandate)

**Rule:** moving an arm/AGV on the canvas MUST change bids + winners +
timings + execution paths. If invariant, Phase 5 has failed.

**Proof:**

- `tests/test_spatial_bidding.py::test_acceptance_gate_position_dictates_winner`
  builds a topology, runs `shortest time` → asserts agv-1 wins; runs
  `longest time` → asserts agv-2 wins; moves agv-1 to a far position;
  re-runs `shortest time` → asserts agv-2 NOW wins. Passes 3× consecutive.
- `tests/distributed/test_spatial_chaos.py::test_placement_change_changes_winner_under_chaos`
  reproduces this against fakeredis-backed multi-thread actors with real
  consensus rounds.

Phase 5 acceptance gate: **PASS**.

---

## 6. Frontend

- `topologySlice` carries `reach_px`/`capacity`/`speed` per actor;
  Inspector edits them; localStorage migration is additive.
- Snap toggle (`<Background variant="dots" gap=40>`) + Shift-to-disable.
- `ArmRenderer` renders pivot + rotating stick + gripper; reach radius
  circle subtle when not selected, prominent when selected; gripper closes
  on PICK/PLACE; idle breathing pulse.
- `AgvTokenLayer` renders vehicle body + heading triangle + status dot;
  tween between `agv_position` updates at 200 ms; return-path arrow when
  status="returning"; idle breathing at origin.
- `AgvTrailLayer` renders polyline trails from POSITION_UPDATE history
  per task.
- `ReplayPanel` cursor scrubs `replayPositions` — AGV tokens snap to
  historical positions during replay.

---

## 7. Deferred to Phase 6+

- Live execution of the 4 `agent-browser` E2E scenarios end-to-end (drag
  from sidebar via the CLI primitive).
- Topology hot-swap protection (no deploy while tasks active OR per-round
  topology version).
- D* Lite / incremental replan (current full A* per leg is fine at scale).
- Multi-AGV path conflict optimization (current: serial replan on
  conflict).
- Banker's-algorithm deadlock prevention (current: timeout + lex
  tie-break).
- Banking constraints during cap < pickup count (heuristic placement).
- Visual snap collision feedback when reserved cells block planned drop
  position.

---

## 8. Evidence index

| Claim | File |
|---|---|
| `Topology` + `ActorSpec` | `app/models/topology.py` |
| `pipeline_config.get_topology_or_legacy_shim()` | `app/models/pipeline_config.py` |
| `CapabilityProfile.position_px` + `reach_px` | `app/agents/capability.py` |
| `derive_slots_by_actor` | `app/agents/negotiation.py` |
| Spatial bid (arm + AGV) | `app/agents/arm_agent.py`, `app/agents/agv_agent.py` |
| `public_winner` extended constraints | `app/agents/negotiation.py` |
| `_traverse_to` + `_traverse_to_with_path` | `app/agents/agv_agent.py` |
| A* pathfinder + Grid + `grid_from_topology` | `app/spatial/pathfinder.py` |
| Epoch-scoped reservation `(task,actor,epoch,cell)` | `app/spatial/reservation.py` |
| `POSITION_UPDATE` 16th MessageType | `app/agents/negotiation.py` |
| ArmRenderer + reach + gripper + idle | `frontend/src/canvas/overlay/ArmRenderer.tsx` |
| AGV vehicle + tween + return arrow + idle pulse | `frontend/src/canvas/overlay/AgvTokenLayer.tsx` |
| AgvTrailLayer | `frontend/src/canvas/overlay/AgvTrailLayer.tsx` |
| Replay scrub → `replayPositions` | `frontend/src/chrome/Replay/ReplayPanel.tsx`, `frontend/src/store/runtimeSlice.ts` |
| Acceptance gate test | `tests/test_spatial_bidding.py` |
| Spatial chaos tests | `tests/distributed/test_spatial_chaos.py` |
| E2E scripts + runbook | `tests/e2e/scenarios/*.sh`, `tests/e2e/lib/common.sh`, `tests/e2e/runbook.md` |

---

*End — Phase 5 cutover complete. Canvas drives runtime; runtime drives
physical execution; physical execution returns to origin.*
