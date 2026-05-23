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
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from app.agents.actor_agent import ActorAgent

logger = logging.getLogger(__name__)

# Movement cadence — keep below ANIM_DURATION (600 ms) × speed_factor.
MOVE_STEP_SLEEP: float = 0.5  # seconds between cell hops
LOAD_SIGNAL_TIMEOUT: float = 120.0
UNLOAD_SIGNAL_TIMEOUT: float = 120.0
LOCK_ACQUIRE_TIMEOUT: float = 60.0
SIGNAL_POLL_INTERVAL: float = 0.25


class AGVAgent(ActorAgent):
    """AGV actor. Bids on the transport slot; score = distance to pickup."""

    def _bid_score(self, slot: dict) -> Optional[float]:
        if slot.get("kind") != "agv":
            return None
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

    # ── Stage 3: real leg execution ────────────────────────────────────────

    def _execute_leg(self, slot_id: str) -> bool:
        """12-step transport sequence driving agv_state/grid_state directly.

        Barrier-checked before each major step and between every cell hop.
        On any halt condition, releases all held locks and stops. On lock/signal
        timeout, calls self.fault() (unrecoverable in Phase 1).

        Phase 4 Stage 3: task_id is resolved from self._current_participant
        (set by ActorAgent._check_consensus_for just before this call), so
        handshake signals are scoped per-task. When _current_participant is None
        (backward-compat path), task_id defaults to "" (single-task legacy key).

        Returns True when the leg completed fully (step 12 reached), False on
        any early-return path (halt, fault, or validity failure).
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

        # Held-lock tracking for clean release on halt.
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
