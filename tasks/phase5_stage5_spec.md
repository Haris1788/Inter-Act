# Phase 5 Stage 5 Committed Sub-Spec — Realtime AGV Movement + Return-to-Origin

AGV traverses canvas in pixel space, not cell-by-cell. AGV publishes
`agv_position` SocketIO events at 10 Hz for live frontend interpolation.
After the place leg, AGV returns to its dropped origin and marks idle.

## Decisions (approved)

- Return-to-origin: always after leg completion.
- Position update cadence: 100 ms live (`agv_position` SocketIO event), 500
  ms bus-log snapshot (Stage 8 adds `POSITION_UPDATE` MessageType — NOT in
  Stage 5; Stage 5 only emits the live SocketIO event).

## Tracks

A (blocking, single track) → B ‖ C (concurrent after A's contract lands).

### Track A — Backend movement loop + `agv_position` event  [single track]

**Files:** `app/agents/agv_agent.py`, `app/__init__.py` (no change expected
unless SocketIO event registration needs a hook), tests.

Design:
- `AGVAgent` captures `self._origin_px = self.profile.position_px` on
  `_execute_leg` entry (the dropped-on-canvas position). This is the
  return-to-origin target.
- Replace the 12-step `_NORMAL_STEPS` movement with px-interpolated
  `_traverse_to(target_x, target_y)`:
  - Step in pixel increments along the straight line from current to
    target. Step size = `MOVE_STEP_PX = 20` (configurable). One step per
    `MOVE_STEP_SLEEP_S = 0.1` (= 10 Hz).
  - Between every step: barrier check (`_execution_valid`), update
    `agv_state.set_cell`(snapped cell for collision lock compatibility),
    emit `agv_position` SocketIO event with `{actor_id, x, y, theta}`.
  - `theta = atan2(dy, dx)` (vehicle heading).
- Step sequence reshaped for spatial mode (when `slot.from_pos` /
  `slot.to_pos` present):
  1. acquire input arm station lock (existing)
  2. `_traverse_to(*slot.from_pos)`  ← pickup approach
  3. wait `poll_load` (existing handshake)
  4. release input station
  5. acquire lane lock (kept for backward compat; per-actor lane locks =
     Stage 7)
  6. `_traverse_to(*slot.to_pos)`     ← transport trip
  7. acquire output arm station lock
  8. wait `poll_unload`
  9. release output station + lane
  10. `_traverse_to(*self._origin_px)` ← **return to origin**
  11. `agv_state.mark_idle`, position back at origin (`set_status idle`).
- Legacy path (no `from_pos`/`to_pos` in slot): existing 12-step cell-based
  loop unchanged.
- `agv_position` SocketIO event: emitted only in spatial mode. Payload:
  `{actor_id, x, y, theta, ts}`. Best-effort like the existing `_emit_log`.
- `agv_state` hash keeps using cell coords for occupancy compat (snap from
  px via `cell = (int(x/cell_px), int(y/cell_px))`). px is the source of
  truth, cell is derived.

### Track B — Frontend velocity tween + AGV vehicle visualization  [parallel]

**Files:** `frontend/src/canvas/AgvTokenLayer.tsx` (Konva or RF — find
existing), `frontend/src/runtime/socketDriver.ts`.

Design:
- New SocketIO handler for `agv_position` event. Updates per-actor
  `{x, y, theta}` in the topology slice runtime sub-state (do NOT mutate
  authored position; runtime position is a separate field).
- `AgvTokenLayer` reads runtime position when present, falls back to
  authored. Eases between updates with `ANIM_DURATION ≈ 200 ms` (less than
  the 100 ms cadence × 2 to avoid catch-up stalls).
- Vehicle shape: rounded rectangle with a small triangle marker for
  heading direction. Use the actor's `color` for body, darker stroke for
  outline. Show `actor_id` label below.
- Optional motion blur trail (subtle): last 5 positions as fading dots.

### Track C — Return-to-origin animation polish + idle pulse  [parallel]

**Files:** `frontend/src/canvas/AgvTokenLayer.tsx` (after B's tween
primitive lands; coordinate via inline comment if file-conflict).

Design:
- When `agv.runtimeStatus === "returning"`, render a subtle path arrow
  from current pos → origin.
- Idle pulse: when at origin AND status idle, gentle 2 s breathing
  animation on the token body.
- Status indicator dot color: idle=green, moving=blue, loading=yellow,
  unloading=yellow, faulted=red, returning=blue dimmer.

## Hard invariants

- AGV motion is in pixel space; cell snapping is a derived view for
  collision lock back-compat.
- `agv_position` is observational only. NO consensus decisions depend on
  it.
- Return-to-origin always runs after the place leg.
- Legacy zone-string slot path (Phase 4) keeps cell-by-cell motion
  unchanged.
- 562 baseline tests pass unchanged. New Stage 5 tests parametrize the
  spatial path.
- No new MessageType in Stage 5 (POSITION_UPDATE = Stage 8 on the bus
  log).

## Tests (Track A)

### `tests/test_agv_spatial_movement.py` (new)
- `test_traverse_emits_positions` — spatial slot; assert ≥ 3
  `agv_position` SocketIO events emitted during traversal.
- `test_traverse_updates_agv_state_cell` — `agv_state.get(agv_id).cell`
  matches snapped px target after traversal.
- `test_returns_to_origin_after_place_leg` — full leg; final
  `agv_state.get(agv_id).cell` matches snapped origin px (idempotent
  to bay).
- `test_origin_captured_from_topology_profile` — actor created with
  `position_px=(500, 500)`; after a leg, returns to (500, 500) (snapped).
- `test_legacy_zone_path_still_uses_cell_motion` — Phase 4-style slot;
  cell-based 12-step path runs unchanged.

## Verify

- `python -m pytest tests/ -q` baseline 562 / 5. After: ≥ 562 + 5 new / 5.
  Zero regressions.
- Frontend `npm run typecheck && npm run build` clean.
- New tests pass 3× consecutively.

## Opus review gate

- AGV physically returns to the dropped origin (verified by test 3 + 4).
- `agv_position` event cadence ≈ 10 Hz under normal traversal load.
- Legacy zone-string AGV motion unchanged (Phase 4 integration tests
  still green).
- No agv_position event published in the legacy cell-by-cell path
  (consensus is unchanged in that mode).
- Barrier (`_execution_valid`) still checked between every motion step.
