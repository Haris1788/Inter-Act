"""AGVAgent — an Automated Guided Vehicle actor in the negotiation runtime.

An AGV fills the `transport` slot. Its bid score is the grid (Manhattan)
distance from its current cell to the pickup station's dock — with equal AGV
speed, the nearest-to-pickup AGV wins, which is goal.md's intended default.

Stage 3: `_execute_leg` drives agv_state / grid_state directly (no HTTP
self-calls, no AGV containers for movement). Route geometry is ported from
scripts/agv_brain._straight_path and _fetch_route_ctx.

Phase 4 Stage 3: `_execute_leg` takes (slot_id, task_id, round). The
execution barrier `_valid()` is resolved via `self._tasks.get(task_id)` —
a lookup on the agent's per-task participant dict.  When task_id is empty or
the participant is not found (e.g. old-style monkeypatched test calls), the
barrier falls through to checking only `_fault_requested`, preserving the
original test contract.

Phase 5 Stage 4: `_bid_score` is now spatial — returns Euclidean approach cost
(position_px → from_pos) + trip cost (from_pos → to_pos). Falls back to the
legacy Manhattan dock-cell path when from_pos/to_pos are absent.

Phase 5 Stage 5: `_execute_leg` has a spatial branch. When the slot dict
carries `from_pos`/`to_pos`, the AGV traverses in pixel space via
`_traverse_to` (10 Hz, 20 px step). After the place leg, it returns to its
dropped origin (`_origin_px`). The legacy 12-step cell-based path is
unchanged when `from_pos` is absent.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Optional

from app.agents.actor_agent import ActorAgent

logger = logging.getLogger(__name__)

# Movement cadence — keep below ANIM_DURATION (600 ms) × speed_factor.
MOVE_STEP_SLEEP: float = 0.5  # seconds between cell hops (legacy cell-based path)
LOAD_SIGNAL_TIMEOUT: float = 120.0
UNLOAD_SIGNAL_TIMEOUT: float = 120.0
LOCK_ACQUIRE_TIMEOUT: float = 60.0
SIGNAL_POLL_INTERVAL: float = 0.25

# Phase 5 Stage 5 — pixel-space traversal constants.
MOVE_STEP_PX: float = 20.0          # pixels per interpolation step
MOVE_STEP_SLEEP_S: float = 0.1      # seconds per step (10 Hz)
CELL_PX: float = 40.0               # px per grid cell (used for cell snapping)

# Phase 5 Stage 7 — A* pathfinding + epoch-scoped reservation constants.
MAX_REPLAN_ATTEMPTS: int = 5        # max replan loops before fault
REPLAN_BACKOFF_S: float = 0.2       # wait between failed replan attempts
PATH_TTL_MS: int = 180_000          # reservation TTL in milliseconds (3 min)
PATH_KEEPALIVE_INTERVAL_S: float = 60.0  # keepalive renewal interval


class AGVAgent(ActorAgent):
    """AGV actor. Bids on the transport slot; score = spatial cost to pickup + trip."""

    def _bid_score(self, slot: dict) -> Optional[float]:
        """Spatial bid score: time-to-deliver = distance / speed.

        When from_pos / to_pos are present (Phase 5 topology path):
            distance = hypot(position_px → from_pos) + hypot(from_pos → to_pos)
            score    = distance / max(profile.speed, 0.001)

        Dividing by speed makes "shortest time" constraints honour the AGV
        speed multiplier (a 2× AGV beats a closer 1× AGV when its time-to-
        deliver is lower). With every AGV at speed=1.0 the score collapses
        to pure distance (Phase 5 acceptance test path).

        Falls back to legacy Manhattan dock-cell path when from_pos / to_pos
        absent.
        """
        if slot.get("kind") != "agv":
            return None
        from_pos = slot.get("from_pos")
        to_pos = slot.get("to_pos")
        if from_pos is not None and to_pos is not None:
            ax, ay = float(self.profile.position_px[0]), float(self.profile.position_px[1])
            fx, fy = float(from_pos[0]), float(from_pos[1])
            tx, ty = float(to_pos[0]), float(to_pos[1])
            approach = math.hypot(ax - fx, ay - fy)
            trip = math.hypot(fx - tx, fy - ty)
            distance = approach + trip
            speed = max(float(self.profile.speed or 1.0), 0.001)
            return distance / speed
        return self._legacy_bid_score(slot)

    def _legacy_bid_score(self, slot: dict) -> Optional[float]:
        """Legacy Manhattan dock-cell scoring (Phase 1–4 backward-compat)."""
        from_zone = slot.get("from_zone")
        dock = self._dock_cell(from_zone)
        if dock is None:
            return None
        if len(self.profile.position) < 2:
            return None
        col, row = self.profile.position[0], self.profile.position[1]
        return abs(col - dock[0]) + abs(row - dock[1])

    def _dock_cell(self, zone: Optional[str]) -> Optional[list[float]]:
        """The [col,row] dock cell of a station zone, from pipeline config."""
        if zone is None:
            return None
        from app.models import pipeline_config

        station = pipeline_config.get_stations().get(zone)
        if not station:
            return None
        cell = station.get("dock") or station.get("cell")
        if not cell:
            return None
        return [float(cell[0]), float(cell[1])]

    # ── Stage 5: pixel-space traversal ────────────────────────────────────

    def _traverse_to(
        self,
        target_x: float,
        target_y: float,
        agv_id: str,
        agv_state,
        grid_state,
        valid_fn,
    ) -> bool:
        """Interpolate from self._last_px to (target_x, target_y) in px space.

        Emits agv_position SocketIO events at MOVE_STEP_SLEEP_S cadence (10 Hz).
        Emits POSITION_UPDATE bus messages at 500 ms cadence for replay fidelity.
        Updates agv_state cell (snapped) between every step.
        Checks valid_fn between every step; returns False on halt.
        Updates self._last_px to (target_x, target_y) on success.
        """
        cur_x, cur_y = self._last_px
        dx = target_x - cur_x
        dy = target_y - cur_y
        total = math.hypot(dx, dy)
        steps = max(1, math.ceil(total / MOVE_STEP_PX))

        for i in range(1, steps + 1):
            # Barrier check between every step.
            if not self._running or not valid_fn():
                return False

            # Linear interpolation.
            t = i / steps
            x = cur_x + dx * t
            y = cur_y + dy * t

            # Heading in radians.
            theta = math.atan2(dy, dx) if total > 0 else 0.0

            # Snap to grid cell for occupancy / lock back-compat.
            cell = [int(x / CELL_PX), int(y / CELL_PX)]

            # Update agv_state (cell + status).
            try:
                agv_state.set_target(agv_id, cell)
                agv_state.set_cell(agv_id, cell)
                agv_state.set_status(agv_id, "moving")
            except (KeyError, ValueError) as exc:
                logger.warning("AGVAgent %s: state update failed in traverse: %s", agv_id, exc)
                return False

            # Emit live position event (best-effort, observational only).
            _emit_agv_position(agv_id, x, y, theta)

            # Emit POSITION_UPDATE bus message at ≤ 2 Hz (500 ms minimum gap).
            # _last_position_update_ts is an instance variable so the cadence is
            # preserved across multiple _traverse_to calls within the same leg.
            # Defaults to 0.0 if not yet set (e.g. when _traverse_to is called
            # directly without going through _execute_leg).
            # Observational only — no actor acts on this message.
            now = time.time()
            if now - getattr(self, "_last_position_update_ts", 0.0) >= 0.5:
                _publish_position_update(self, agv_id, x, y, theta)
                self._last_position_update_ts = now

            time.sleep(MOVE_STEP_SLEEP_S)

        # Success — record final position.
        self._last_px = (target_x, target_y)
        # Snap final cell.
        final_cell = [int(target_x / CELL_PX), int(target_y / CELL_PX)]
        try:
            agv_state.set_cell(agv_id, final_cell)
        except (KeyError, ValueError):
            pass
        return True

    # ── Stage 7: A* pathfinding traversal ─────────────────────────────────

    def _traverse_to_with_path(
        self,
        target_x: float,
        target_y: float,
        agv_id: str,
        agv_state,
        grid_state,
        valid_fn,
        task_id: str,
    ) -> bool:
        """Traverse from self._last_px to (target_x, target_y) using A* + reservation.

        Algorithm:
          1. Compute epoch from ReservationStore.
          2. Build grid from topology.
          3. Run A* with obstacles from other actors' reservations.
          4. On path=None: wait REPLAN_BACKOFF_S and retry (up to MAX_REPLAN_ATTEMPTS).
          5. Reserve the path cells; on conflict bump epoch and retry.
          6. Start PathKeepalive for TTL renewal.
          7. Traverse cell-by-cell (pixel interpolation via _traverse_to per hop).
          8. Release reservation in try/finally so halt path also releases.

        Falls back to bare _traverse_to when start_cell == goal_cell (no-op move).
        """
        from app.spatial.pathfinder import a_star, grid_from_topology
        from app.spatial.reservation import get_store, PathKeepalive
        from app.models import pipeline_config

        store = get_store()
        topology = pipeline_config.get_topology_or_legacy_shim()

        start_cell = topology.cell_for(self._last_px[0], self._last_px[1])
        goal_cell = topology.cell_for(target_x, target_y)

        # Trivial: already at goal cell — still do pixel interpolation to land
        # exactly on the target pixel, but no path reservation needed.
        if start_cell == goal_cell:
            return self._traverse_to(target_x, target_y, agv_id, agv_state, grid_state, valid_fn)

        epoch = store.next_epoch(task_id, agv_id)

        for attempt in range(MAX_REPLAN_ATTEMPTS):
            if not self._running or not valid_fn():
                return False

            obstacles_others = store.held_by_others(task_id, agv_id)
            grid = grid_from_topology(topology, extra_obstacles=())
            path = a_star(grid, start_cell, goal_cell, blocked=obstacles_others)

            if path is None:
                logger.info(
                    "AGVAgent %s: A* found no path (attempt %d/%d) — backing off %.1fs",
                    agv_id, attempt + 1, MAX_REPLAN_ATTEMPTS, REPLAN_BACKOFF_S,
                )
                time.sleep(REPLAN_BACKOFF_S)
                continue

            reserved = store.reserve(task_id, agv_id, epoch, path, ttl_ms=PATH_TTL_MS)
            if not reserved:
                # Conflict — bump epoch and retry.
                logger.info(
                    "AGVAgent %s: reservation conflict (attempt %d/%d) — bumping epoch",
                    agv_id, attempt + 1, MAX_REPLAN_ATTEMPTS,
                )
                epoch = store.next_epoch(task_id, agv_id)
                continue

            # Emit agv_path for frontend spatial overlay (observational only).
            try:
                from app import socketio
                socketio.emit("agv_path", {
                    "actor_id": agv_id,
                    "task_id": task_id,
                    "epoch": epoch,
                    "path_cells": [[c[0], c[1]] for c in path],
                    "ts": time.time(),
                })
            except Exception:
                pass  # best-effort, no consensus impact

            # Got path + reservation.  Traverse cell-by-cell.
            keepalive = PathKeepalive(
                store, task_id, agv_id, epoch,
                ttl_ms=PATH_TTL_MS, interval_s=PATH_KEEPALIVE_INTERVAL_S,
            )
            keepalive.start()
            try:
                # path[0] == start_cell (we're already there); skip it.
                for cell in path[1:]:
                    cx = (cell[0] + 0.5) * CELL_PX
                    cy = (cell[1] + 0.5) * CELL_PX
                    ok = self._traverse_to(cx, cy, agv_id, agv_state, grid_state, valid_fn)
                    if not ok:
                        # _traverse_to already returned False on halt;
                        # release is in the finally block.
                        return False
                # Final pixel-exact landing on target (may differ from cell center).
                ok = self._traverse_to(target_x, target_y, agv_id, agv_state, grid_state, valid_fn)
                if not ok:
                    return False
            finally:
                keepalive.stop()
                store.release(task_id, agv_id, epoch)

            return True

        # All attempts exhausted.
        self.fault(f"{agv_id}: A* path replan exhausted after {MAX_REPLAN_ATTEMPTS} attempts")
        return False

    # ── Stage 3: real leg execution ────────────────────────────────────────

    def _execute_leg(self, slot_id: str) -> bool:
        """Transport sequence driving agv_state/grid_state directly.

        Phase 5 Stage 5: two branches.
        - Spatial (slot has from_pos/to_pos): pixel-space traversal via
          _traverse_to. AGV returns to its dropped origin after the place leg.
        - Legacy (no from_pos): existing 12-step cell-based loop UNCHANGED.

        Barrier-checked before each major step and between every motion step.
        On any halt condition, releases all held locks and stops. On lock/signal
        timeout, calls self.fault() (unrecoverable in Phase 1).

        Phase 4 Stage 3: task_id is resolved from self._current_participant
        (set by ActorAgent._check_consensus_for just before this call), so
        handshake signals are scoped per-task. When _current_participant is None
        (backward-compat path), task_id defaults to "" (single-task legacy key).

        Returns True when the leg completed fully, False on any early-return
        path (halt, fault, or validity failure).
        Non-transport slots and missing agv_state are no-op successes (True).
        """
        if slot_id != "transport":
            logger.debug("AGVAgent %s: ignoring non-transport slot %s", self.actor_id, slot_id)
            return True

        from app.models import agv_state, grid_state, pipeline_config
        from app.routes.agv_routes import poll_load, clear_load, poll_unload, clear_unload

        agv_id = self.actor_id

        # Resolve task_id for per-task signal scoping.
        _participant = self._current_participant
        task_id = _participant.task_id if _participant is not None else ""

        # ── Guard: skip leg if AGV is not registered in agv_state ───────────
        # (happens in tests without _execution_env — fall through to stub behaviour)
        if agv_state.get(agv_id) is None:
            logger.debug(
                "AGVAgent %s: not registered in agv_state — leg skipped (no runtime)",
                agv_id,
            )
            return True

        # ── Capture origin px at leg entry (Phase 5 Stage 5 return-to-origin) ─
        self._origin_px: tuple[float, float] = (
            float(self.profile.position_px[0]),
            float(self.profile.position_px[1]),
        )
        # _last_px tracks current pixel position during spatial traversal.
        self._last_px: tuple[float, float] = self._origin_px
        # _last_position_update_ts is reset at leg start so the first step
        # always emits a POSITION_UPDATE immediately. Preserved across multiple
        # _traverse_to calls within the same leg for correct 500 ms cadence.
        self._last_position_update_ts: float = 0.0

        # ── Resolve slot dict for spatial detection ──────────────────────────
        slot_dict: dict = {}
        if _participant is not None and _participant.task:
            for s in _participant.task.get("slots", []):
                if s.get("slot_id") == slot_id:
                    slot_dict = s
                    break

        from_pos = slot_dict.get("from_pos")
        to_pos = slot_dict.get("to_pos")
        _spatial = from_pos is not None

        # ── Held-lock tracking for clean release on halt ─────────────────────
        held_station: Optional[str] = None
        held_lane: bool = False

        def _halt(reason: str) -> None:
            logger.info("AGVAgent %s: halt — %s", agv_id, reason)
            _emit_log(agv_id, f"AGV {agv_id} halting execution: {reason}")
            if held_lane:
                grid_state.lane().release(agv_id)
            if held_station:
                grid_state.station_lock(held_station).release(agv_id)

        def _valid() -> bool:
            """Execution barrier: use self._current_participant for validity.

            self._current_participant is set by ActorAgent._check_consensus_for
            just before calling _execute_leg. When it is None (e.g. an old
            monkeypatched test call), fall back to checking only _fault_requested.
            """
            p = self._current_participant
            if p is not None:
                return p.execution_valid(slot_id)
            # Backward-compat fallback: only block on agent-level fault.
            return not self._fault_requested

        def _move_cell(cell: list, status: str = "moving") -> bool:
            """Move AGV one cell; return False if halted or state missing."""
            if not self._running or not _valid():
                return False
            try:
                agv_state.set_target(agv_id, cell)
                agv_state.set_cell(agv_id, cell)
                rec = agv_state.set_status(agv_id, status)
            except (KeyError, ValueError) as exc:
                logger.warning("AGVAgent %s: state update failed: %s", agv_id, exc)
                return False
            grid_state.set_cell(agv_id, cell)
            _emit_agv(rec)
            time.sleep(MOVE_STEP_SLEEP)
            return True

        # ════════════════════════════════════════════════════════════════════
        # SPATIAL BRANCH (Phase 5 Stage 5) — pixel-space traversal
        # ════════════════════════════════════════════════════════════════════
        if _spatial:
            logger.info(
                "AGVAgent %s: spatial leg — from_pos=%s to_pos=%s origin=%s",
                agv_id, from_pos, to_pos, self._origin_px,
            )

            # ── Step 1: acquire input arm station ────────────────────────────
            if not _valid():
                return False
            logger.info("AGVAgent %s: spatial step 1 — acquire input_arm station", agv_id)
            ok = grid_state.station_lock("input_arm").acquire(
                agv_id,
                timeout=LOCK_ACQUIRE_TIMEOUT,
                cancel_check=lambda: agv_state.is_alive(agv_id) and _valid(),
            )
            if not ok:
                if not _valid():
                    _halt("input_arm station acquire cancelled by validity check")
                else:
                    self.fault(f"{agv_id}: input_arm station acquire timed out")
                return False
            held_station = "input_arm"

            # ── Step 2: traverse to pickup position (A* when task_id available) ─
            logger.info("AGVAgent %s: spatial step 2 — traverse to from_pos", agv_id)
            if task_id:
                ok = self._traverse_to_with_path(
                    float(from_pos[0]), float(from_pos[1]),
                    agv_id, agv_state, grid_state, _valid, task_id,
                )
            else:
                ok = self._traverse_to(
                    float(from_pos[0]), float(from_pos[1]),
                    agv_id, agv_state, grid_state, _valid,
                )
            if not ok:
                _halt("validity check failed during traverse to from_pos")
                return False

            # ── Step 3: poll load_ready ──────────────────────────────────────
            logger.info("AGVAgent %s: spatial step 3 — waiting for load signal", agv_id)
            rec = agv_state.set_status(agv_id, "loading")
            if rec:
                _emit_agv(rec)
            deadline = time.time() + LOAD_SIGNAL_TIMEOUT
            while not poll_load(agv_id, task_id=task_id):
                if not self._running:
                    _halt("agent stopped while waiting for load signal")
                    return False
                if not _valid():
                    _halt("validity check failed while waiting for load signal")
                    return False
                if time.time() >= deadline:
                    grid_state.station_lock("input_arm").release(agv_id)
                    held_station = None
                    self.fault(f"{agv_id}: load signal timed out after {LOAD_SIGNAL_TIMEOUT}s")
                    return False
                time.sleep(SIGNAL_POLL_INTERVAL)
            clear_load(agv_id, task_id=task_id)
            logger.info("AGVAgent %s: load signal received and cleared", agv_id)

            # ── Step 4: release input station ────────────────────────────────
            grid_state.station_lock("input_arm").release(agv_id)
            held_station = None
            _emit_log(agv_id, f"{agv_id} released input_arm station")

            # ── Step 5: acquire lane lock ────────────────────────────────────
            if not _valid():
                _halt("validity check failed before lane acquire")
                return False
            logger.info("AGVAgent %s: spatial step 5 — acquire lane", agv_id)
            ok = grid_state.lane().acquire(
                agv_id,
                timeout=LOCK_ACQUIRE_TIMEOUT,
                cancel_check=lambda: agv_state.is_alive(agv_id) and _valid(),
            )
            if not ok:
                if not _valid():
                    _halt("lane acquire cancelled by validity check")
                else:
                    self.fault(f"{agv_id}: lane acquire timed out")
                return False
            held_lane = True

            # ── Step 6: traverse to dropoff position (A* when task_id available) ─
            logger.info("AGVAgent %s: spatial step 6 — traverse to to_pos", agv_id)
            if task_id:
                ok = self._traverse_to_with_path(
                    float(to_pos[0]), float(to_pos[1]),
                    agv_id, agv_state, grid_state, _valid, task_id,
                )
            else:
                ok = self._traverse_to(
                    float(to_pos[0]), float(to_pos[1]),
                    agv_id, agv_state, grid_state, _valid,
                )
            if not ok:
                _halt("validity check failed during traverse to to_pos")
                return False

            # ── Step 7: acquire output arm station ───────────────────────────
            if not _valid():
                _halt("validity check failed before output station acquire")
                return False
            logger.info("AGVAgent %s: spatial step 7 — acquire output_arm station", agv_id)
            ok = grid_state.station_lock("output_arm").acquire(
                agv_id,
                timeout=LOCK_ACQUIRE_TIMEOUT,
                cancel_check=lambda: agv_state.is_alive(agv_id) and _valid(),
            )
            if not ok:
                if not _valid():
                    _halt("output_arm station acquire cancelled by validity check")
                else:
                    grid_state.lane().release(agv_id)
                    held_lane = False
                    self.fault(f"{agv_id}: output_arm station acquire timed out")
                return False
            held_station = "output_arm"

            # ── Step 8: poll unload_ready ────────────────────────────────────
            logger.info("AGVAgent %s: spatial step 8 — waiting for unload signal", agv_id)
            rec = agv_state.set_status(agv_id, "unloading")
            if rec:
                _emit_agv(rec)
            deadline = time.time() + UNLOAD_SIGNAL_TIMEOUT
            while not poll_unload(agv_id, task_id=task_id):
                if not self._running:
                    _halt("agent stopped while waiting for unload signal")
                    return False
                if not _valid():
                    _halt("validity check failed while waiting for unload signal")
                    return False
                if time.time() >= deadline:
                    grid_state.station_lock("output_arm").release(agv_id)
                    held_station = None
                    grid_state.lane().release(agv_id)
                    held_lane = False
                    self.fault(f"{agv_id}: unload signal timed out after {UNLOAD_SIGNAL_TIMEOUT}s")
                    return False
                time.sleep(SIGNAL_POLL_INTERVAL)
            clear_unload(agv_id, task_id=task_id)
            logger.info("AGVAgent %s: unload signal received and cleared", agv_id)

            # ── Step 9: release output station + lane ────────────────────────
            grid_state.station_lock("output_arm").release(agv_id)
            held_station = None
            grid_state.lane().release(agv_id)
            held_lane = False
            _emit_log(agv_id, f"{agv_id} released output_arm station and lane")

            # ── Step 10: return to origin ────────────────────────────────────
            logger.info(
                "AGVAgent %s: spatial step 10 — return to origin %s",
                agv_id, self._origin_px,
            )
            rec = agv_state.set_status(agv_id, "returning")
            if rec:
                _emit_agv(rec)
            if task_id:
                ok = self._traverse_to_with_path(
                    self._origin_px[0], self._origin_px[1],
                    agv_id, agv_state, grid_state, _valid, task_id,
                )
            else:
                ok = self._traverse_to(
                    self._origin_px[0], self._origin_px[1],
                    agv_id, agv_state, grid_state, _valid,
                )
            if not ok:
                # Locks already released; still safe to stop.
                logger.info("AGVAgent %s: halt on return-to-origin path", agv_id)
                return False

            # ── Step 11: mark idle ───────────────────────────────────────────
            agv_state.mark_idle(agv_id)
            rec = agv_state.get(agv_id)
            if rec:
                _emit_agv(rec)
            logger.info(
                "AGVAgent %s: spatial transport leg complete — idle at origin %s",
                agv_id, self._origin_px,
            )
            return True

        # ════════════════════════════════════════════════════════════════════
        # LEGACY BRANCH — 12-step cell-based loop (UNCHANGED from Phase 4)
        # ════════════════════════════════════════════════════════════════════

        # ── Resolve route context from pipeline_config ──────────────────────
        route_ctx = self._build_route_ctx()
        if route_ctx is None:
            logger.warning(
                "AGVAgent %s: cannot build route context — leg skipped (no warehouse config)",
                agv_id,
            )
            return True

        bay = route_ctx["bay"]
        route_to_input = route_ctx["route_to_input"]
        lane_cells = route_ctx["lane_cells"]
        output_dock = route_ctx["output_dock"]
        route_to_bay = route_ctx["route_to_bay"]

        # ── Step 1: acquire input station ───────────────────────────────────
        if not _valid():
            return False

        logger.info("AGVAgent %s: step 1 — acquire input_arm station", agv_id)
        ok = grid_state.station_lock("input_arm").acquire(
            agv_id,
            timeout=LOCK_ACQUIRE_TIMEOUT,
            cancel_check=lambda: agv_state.is_alive(agv_id) and _valid(),
        )
        if not ok:
            if not _valid():
                _halt("input_arm station acquire cancelled by validity check")
            else:
                self.fault(f"{agv_id}: input_arm station acquire timed out")
            return False
        held_station = "input_arm"

        # ── Step 2: move bay → input dock, cell by cell ─────────────────────
        logger.info("AGVAgent %s: step 2 — move to input dock", agv_id)
        for cell in route_to_input:
            if not _move_cell(cell):
                _halt("validity check failed during move to input")
                return False

        # ── Step 3: poll load_ready (arm signals package placed on AGV) ─────
        logger.info("AGVAgent %s: step 3 — waiting for load signal", agv_id)
        rec = agv_state.set_status(agv_id, "loading")
        if rec:
            _emit_agv(rec)

        deadline = time.time() + LOAD_SIGNAL_TIMEOUT
        while not poll_load(agv_id, task_id=task_id):
            if not self._running:
                _halt("agent stopped while waiting for load signal")
                return False
            if not _valid():
                _halt("validity check failed while waiting for load signal")
                return False
            if time.time() >= deadline:
                # Release the input_arm station before faulting — _do_fault
                # only withdraws negotiation slots, it does NOT release grid_state locks.
                grid_state.station_lock("input_arm").release(agv_id)
                held_station = None
                self.fault(f"{agv_id}: load signal timed out after {LOAD_SIGNAL_TIMEOUT}s")
                return False
            time.sleep(SIGNAL_POLL_INTERVAL)

        clear_load(agv_id, task_id=task_id)
        logger.info("AGVAgent %s: load signal received and cleared", agv_id)

        # ── Step 4: release input station ───────────────────────────────────
        grid_state.station_lock("input_arm").release(agv_id)
        held_station = None
        _emit_log(agv_id, f"{agv_id} released input_arm station")

        # ── Step 5: acquire lane ─────────────────────────────────────────────
        if not _valid():
            _halt("validity check failed before lane acquire")
            return False

        logger.info("AGVAgent %s: step 5 — acquire lane", agv_id)
        ok = grid_state.lane().acquire(
            agv_id,
            timeout=LOCK_ACQUIRE_TIMEOUT,
            cancel_check=lambda: agv_state.is_alive(agv_id) and _valid(),
        )
        if not ok:
            if not _valid():
                _halt("lane acquire cancelled by validity check")
            else:
                self.fault(f"{agv_id}: lane acquire timed out")
            return False
        held_lane = True

        # ── Step 6: traverse lane input → output dock ────────────────────────
        logger.info("AGVAgent %s: step 6 — traverse lane", agv_id)
        for cell in lane_cells:
            if not _move_cell(cell):
                _halt("validity check failed during lane traverse")
                return False

        # ── Step 7: acquire output station ──────────────────────────────────
        if not _valid():
            _halt("validity check failed before output station acquire")
            return False

        logger.info("AGVAgent %s: step 7 — acquire output_arm station", agv_id)
        ok = grid_state.station_lock("output_arm").acquire(
            agv_id,
            timeout=LOCK_ACQUIRE_TIMEOUT,
            cancel_check=lambda: agv_state.is_alive(agv_id) and _valid(),
        )
        if not ok:
            if not _valid():
                _halt("output_arm station acquire cancelled by validity check")
            else:
                # Release the lane before faulting — _do_fault does NOT release
                # grid_state locks. held_station is None here (station acquire failed).
                grid_state.lane().release(agv_id)
                held_lane = False
                self.fault(f"{agv_id}: output_arm station acquire timed out")
            return False
        held_station = "output_arm"

        # ── Step 8: move to output dock cell ────────────────────────────────
        logger.info("AGVAgent %s: step 8 — move to output dock", agv_id)
        if not _move_cell(output_dock):
            _halt("validity check failed during move to output dock")
            return False

        # ── Step 9: poll unload_ready (place arm signals ready to receive) ──
        logger.info("AGVAgent %s: step 9 — waiting for unload signal", agv_id)
        rec = agv_state.set_status(agv_id, "unloading")
        if rec:
            _emit_agv(rec)

        deadline = time.time() + UNLOAD_SIGNAL_TIMEOUT
        while not poll_unload(agv_id, task_id=task_id):
            if not self._running:
                _halt("agent stopped while waiting for unload signal")
                return False
            if not _valid():
                _halt("validity check failed while waiting for unload signal")
                return False
            if time.time() >= deadline:
                # Release both output_arm station and lane before faulting —
                # _do_fault does NOT release grid_state locks.
                grid_state.station_lock("output_arm").release(agv_id)
                held_station = None
                grid_state.lane().release(agv_id)
                held_lane = False
                self.fault(f"{agv_id}: unload signal timed out after {UNLOAD_SIGNAL_TIMEOUT}s")
                return False
            time.sleep(SIGNAL_POLL_INTERVAL)

        clear_unload(agv_id, task_id=task_id)
        logger.info("AGVAgent %s: unload signal received and cleared", agv_id)

        # ── Step 10: release output station + lane ───────────────────────────
        grid_state.station_lock("output_arm").release(agv_id)
        held_station = None
        grid_state.lane().release(agv_id)
        held_lane = False
        _emit_log(agv_id, f"{agv_id} released output_arm station and lane")

        # ── Step 11: return output → bay, cell by cell ───────────────────────
        logger.info("AGVAgent %s: step 11 — return to bay", agv_id)
        for cell in route_to_bay:
            if not _move_cell(cell, "returning"):
                # Validity check failed on return — still safe to stop here,
                # locks are already released.
                logger.info("AGVAgent %s: halt on return path (validity false)", agv_id)
                return False

        # ── Step 12: mark idle ───────────────────────────────────────────────
        agv_state.mark_idle(agv_id)
        rec = agv_state.get(agv_id)
        if rec:
            _emit_agv(rec)
        logger.info("AGVAgent %s: transport leg complete — idle at bay", agv_id)
        return True

    # ── Route helpers ──────────────────────────────────────────────────────

    def _build_route_ctx(self) -> Optional[dict]:
        """Build route context from pipeline_config (mirrors agv_brain._fetch_route_ctx).

        Returns None if the AGV or station config is missing.
        """
        from app.models import agv_state, pipeline_config

        agv_id = self.actor_id
        agv_cfg = pipeline_config.get_agv(agv_id)
        if agv_cfg is None:
            return None

        stations = pipeline_config.get_stations()
        lane_cells_cfg = pipeline_config.get_lane_cells()

        bay = list(agv_cfg.get("bay", [0, 0]))
        in_st = stations.get("input_arm", {})
        out_st = stations.get("output_arm", {})
        input_dock = list(in_st.get("dock") or in_st.get("cell", bay))
        output_dock = list(out_st.get("dock") or out_st.get("cell", bay))
        lane_cells = [list(c) for c in lane_cells_cfg] or _straight_path(input_dock, output_dock)

        # Use the AGV's live position as the start (profile position at bid time).
        agv_rec = agv_state.get(agv_id)
        if agv_rec and agv_rec.get("cell"):
            start_cell = list(agv_rec["cell"])
        else:
            start_cell = bay

        return {
            "bay": bay,
            "route_to_input": _straight_path(start_cell, input_dock),
            "lane_cells": lane_cells,
            "output_dock": output_dock,
            "route_to_bay": _straight_path(output_dock, bay),
        }


def _straight_path(start: list, end: list) -> list:
    """Simple rectilinear path: move columns first, then rows.
    Ported from scripts/agv_brain._straight_path.
    """
    path: list = []
    sc, sr = int(start[0]), int(start[1])
    ec, er = int(end[0]), int(end[1])

    dc = 1 if ec >= sc else -1
    for c in range(sc, ec + dc, dc):
        path.append([c, sr])

    dr = 1 if er > sr else -1
    for r in range(sr + dr, er + dr, dr):
        path.append([ec, r])

    return path if path else [list(end)]


def _emit_agv(rec: dict) -> None:
    """Best-effort SocketIO emit for agv_update."""
    try:
        from app import socketio
        from app.models import pipeline_config

        def _px(cell):
            return pipeline_config.cell_to_px(cell) if cell else None

        socketio.emit("agv_update", {
            **rec,
            "px": _px(rec.get("cell")),
            "target_px": _px(rec.get("target_cell")),
            "bay_px": _px(rec.get("bay")),
        })
    except Exception:
        pass


def _emit_log(source: str, message: str) -> None:
    """Best-effort SocketIO log_message emit."""
    try:
        from app import socketio
        from datetime import datetime
        socketio.emit("log_message", {
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "source": source,
            "message": message,
        })
    except Exception:
        pass


def _emit_agv_position(actor_id: str, x: float, y: float, theta: float) -> None:
    """Best-effort SocketIO agv_position emit (Phase 5 Stage 5).

    Observational only — no consensus decisions depend on this event.
    Mirrors the _emit_log / _emit_agv best-effort pattern: never raises.
    """
    try:
        from app import socketio
        socketio.emit("agv_position", {
            "actor_id": actor_id,
            "x": x,
            "y": y,
            "theta": theta,
            "ts": time.time(),
        })
    except Exception:
        pass


def _publish_position_update(agent: "AGVAgent", agv_id: str, x: float, y: float, theta: float) -> None:
    """Publish a POSITION_UPDATE message to the negotiation bus at 500 ms cadence.

    Phase 5 Stage 8 — observational only. Emitted from _traverse_to during
    spatial AGV motion for replay fidelity. No actor changes decisions based on
    POSITION_UPDATE messages; they are a bus-visible audit trail for replay/scrub.

    Uses _current_participant to derive task_id and negotiation_round. When no
    participant is set (e.g. in isolated unit tests), uses empty-string defaults.
    """
    from app.agents.negotiation import Message, MessageType

    participant = agent._current_participant
    task_id = participant.task_id if participant is not None else ""
    negotiation_round = participant.round if participant is not None else 0

    ts = time.time()
    try:
        agent._safe_publish(
            Message(
                type=MessageType.POSITION_UPDATE,
                task_id=task_id,
                negotiation_round=negotiation_round,
                sender=agv_id,
                payload={"x": x, "y": y, "theta": theta, "ts": ts},
            )
        )
    except Exception:
        pass
