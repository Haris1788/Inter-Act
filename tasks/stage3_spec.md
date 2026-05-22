# Stage 3 Committed Sub-Spec — Real `_execute_leg` + Barrier + Higher-Round Halt

Phase 3 Tasks 4 + 5 + 8. Single coupled track — no parallel. All execution is **in-backend**
(agents are backend-process threads); no HTTP self-calls, no AGV containers for movement.

## Decisions (approved)

- AGVAgent drives `agv_state` / `grid_state` **directly** and emits `agv_update` via SocketIO.
  `scripts/agv_server.py` + `scripts/agv_brain.py` become dead (deleted in Stage 4 / Task 6) —
  their *movement geometry* is absorbed here.
- Leg sequencing reuses the existing **load/unload handshake** (`agv_routes.py` `_signals`).
- Arms execute in-backend too (angle-sweep animation reused, extracted out of `dispatch_routes`).

## Shared prerequisite — handshake + motion helpers

1. **Promote the signal registry.** In `agv_routes.py`, expose public functions so in-process
   agents can use the handshake without HTTP: `signal_load(agv_id, object_id)`,
   `poll_load(agv_id) -> bool`, `clear_load(agv_id)`, and the unload trio. Reuse the existing
   `_signals` dict + `_signals_lock` — do NOT create a parallel registry. The HTTP routes stay
   and call the same functions.
2. **Extract arm motion.** Create `app/agents/actuation.py` with a single reusable
   `sweep_arm(arm_id, target_angle, steps, step_sleep)` that, per step, calls
   `arm_state.set_angle` and emits `arm_angle_update`. This survives `dispatch_routes` deletion.
   PICK angle 180, PLACE angle 0, REST 90 (constants per `CLAUDE.md`).

## Task 5 — execution barrier (`_execution_valid`)

New `ActorAgent` method `_execution_valid(slot_id) -> bool`. Re-derive from a fresh
`self.bus.history()`:
- consensus still complete: `slots_complete(required, accepted_actors(history, task_id, round))`.
- own slot still held: `accepted_actors(...)[slot_id] == self.actor_id`.
- no higher round: no `TASK_ANNOUNCE` for `task_id` with `negotiation_round > self._round`.
- not fault-requested: `not self._fault_requested`.
Returns False on any violation. `bus.history()` is the live cross-thread truth — other agents'
publishes land there even while this agent's loop is blocked inside `_execute_leg`.

## Task 8 — mid-execution higher-round halt

Real I/O is **chunked** (AGV one cell per step; arm one angle increment per step). Before each
leg and **between every chunk**, call `_execution_valid(slot_id)`. On False → **halt**:
- AGV: release any held `grid_state` lane + station locks, set status (leave non-DONE), stop.
- Arm: stop the sweep, stop.
- Never continue stale execution. Log a `log_message` halt line.
Halt is safe and idempotent. Full distributed recovery is Phase 2 — only the halt is in scope.

## Task 4 — `AGVAgent._execute_leg("transport")`

Absorb `agv_brain._NORMAL_STEPS` geometry; drop the qwen tactical brain entirely (the agent
already decided by winning the slot — movement is deterministic geometry, which is legitimate:
"agent decides, runtime performs motion"). Route from `pipeline_config.get_warehouse_scene()`;
reuse a `_straight_path` rectilinear helper (port from `agv_brain._straight_path`).

Sequence (barrier-checked between every cell hop):
1. `station_lock("input_arm").acquire(agv_id, cancel_check=agv_state.is_alive)`
2. move bay → input dock, cell-by-cell (`agv_state.set_cell` + `grid_state.set_cell` + emit)
3. poll `load_ready` (timeout ~120 s) → on ready `clear_load`
4. release input station
5. `lane().acquire(...)`
6. traverse lane cells input → output dock
7. `station_lock("output_arm").acquire(...)`
8. move to output dock cell
9. poll `unload_ready` (timeout) → on ready `clear_unload`
10. release output station + lane
11. return output → bay cell-by-cell
12. `agv_state.mark_idle(agv_id)`

On any lock timeout / signal timeout → treat as leg failure: release everything, halt (do not
silently proceed). A genuine unrecoverable failure may `self.fault(reason)`.

## Task 4 — `ArmAgent._execute_leg`

The arm reads the winning AGV id from `accepted_actors(history, task_id, round)["transport"]`.
- `pickup` slot: `sweep_arm(arm_id, PICK)` then `sweep_arm(arm_id, REST)`; then `signal_load(agv,
  object_id)` so the waiting AGV proceeds.
- `place` slot: `signal_unload(agv)` (ready to receive) → wait until the AGV has cleared unload
  (`poll`/signal consumed, timeout-bounded) → `sweep_arm(arm_id, PLACE)` then `sweep_arm(REST)`.
Barrier-checked between sweeps. Emergent sequencing: AGV step 3 blocks on the pickup arm's
`signal_load`; AGV step 9 blocks on the place arm's `signal_unload` — no central sequencer.

## Constraints

- Do NOT add a coordinator, a sequencer, or any component that owns leg order.
- `_execute_leg` stays an override on `ArmAgent` / `AGVAgent`; the base keeps the stub.
- One task at a time (Phase 1). Concurrent negotiations are Phase 2 — do not build for them.
- Keep `MOVE_STEP_SLEEP`-style cadence so the frontend easing does not stall.

## Verify

- Extend `tests/test_negotiation_integration.py`: a converged task drives real `agv_state` cell
  changes and arm angle changes (monkeypatch a minimal warehouse scene); assert the AGV ends at
  its bay idle and both arms swept. Fault mid-execution → AGV halts and releases the lane.
- Higher-round announce mid-execution → executing agent halts, does not finish its leg.
- `python -m pytest tests/ -q` — no regressions beyond the known 5 pre-existing failures.

## Opus review gate

Execution-barrier verification + stale-execution proof: no leg completes after a superseding
round; every halt releases its locks; no deadlock between the three legs.
