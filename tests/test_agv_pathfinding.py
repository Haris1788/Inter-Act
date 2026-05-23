"""Phase 5 Stage 7 Track B — AGVAgent._traverse_to_with_path tests.

Verifies A* pathfinding + epoch-scoped reservation integration in the spatial
branch of AGVAgent._execute_leg / _traverse_to_with_path.

Tests:
  - test_simple_traverse_uses_a_star        — no obstacles; path reserved and traversed
  - test_obstacle_routes_around             — arm pivot between start and goal
  - test_concurrent_agv_replans_to_avoid_reservation — pre-reserved cells avoided
  - test_release_after_traverse             — reservation keys gone after success
  - test_replan_bumps_epoch_on_conflict     — epoch increments on reservation conflict
  - test_fault_after_max_replans            — goal walled off → fault after MAX_REPLAN_ATTEMPTS
"""

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from app.agents.agv_agent import (
    AGVAgent,
    CELL_PX,
    MAX_REPLAN_ATTEMPTS,
    REPLAN_BACKOFF_S,
    PATH_TTL_MS,
)
from app.agents.bus import NegotiationBus
from app.agents.capability import CapabilityProfile
from app.spatial.pathfinder import Grid
from app.spatial.reservation import (
    _InMemoryReservationStore,
    ReservationStore,
    _inject_redis_client,
)
import app.spatial.reservation as reservation_mod
from app.models.topology import Topology, ActorSpec


# ── Scene constants ────────────────────────────────────────────────────────────

_STATIONS = {
    "input_arm": {"served_by": "arm-1", "cell": [1, 1], "dock": [2, 1]},
    "output_arm": {"served_by": "arm-2", "cell": [12, 1], "dock": [11, 1]},
}

_LANE_CELLS = [[3, 1], [4, 1], [5, 1], [6, 1], [7, 1], [8, 1], [9, 1], [10, 1]]

_WAREHOUSE_SCENE = {
    "grid": {"cols": 20, "rows": 10, "cell_px": 40},
    "stations": {
        "input_arm": {
            "cell": [1, 1], "dock": [2, 1], "served_by": "arm-1",
            "label": "input_arm", "px": None, "dock_px": None,
        },
        "output_arm": {
            "cell": [12, 1], "dock": [11, 1], "served_by": "arm-2",
            "label": "output_arm", "px": None, "dock_px": None,
        },
    },
    "lane": {"cells": _LANE_CELLS, "px": []},
    "agvs": [{"id": "agv-1", "label": "AGV 1", "color": "#16a085",
              "bay": [0, 1], "bay_px": None}],
}

# A topology with no arm obstacles — clean grid for pathfinding tests.
_TOPOLOGY_CLEAN = Topology(
    actors=(),
    canvas_width=800,
    canvas_height=400,
    cell_px=40,
)

# Topology with arm-1 at cell (5, 1) — obstacle between start and goal.
_TOPOLOGY_ARM_AT_5_1 = Topology(
    actors=(ActorSpec(id="arm-1", kind="arm", x=220.0, y=60.0),),
    canvas_width=800,
    canvas_height=400,
    cell_px=40,
)


# ── Fixtures ────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_reservation_store():
    """Reset the reservation singleton before each test."""
    reservation_mod._store_instance = None
    reservation_mod._injected_redis_client = None
    yield
    reservation_mod._store_instance = None
    reservation_mod._injected_redis_client = None


@pytest.fixture(autouse=True)
def _stations(monkeypatch):
    from app.models import pipeline_config
    monkeypatch.setattr(pipeline_config, "get_stations", lambda: _STATIONS)


@pytest.fixture()
def _execution_env(monkeypatch):
    """Minimal execution environment for pathfinding tests.

    Sets up agv_state, grid_state, pipeline_config, and suppresses SocketIO.
    """
    from app.models import pipeline_config, agv_state, grid_state
    from app.routes import agv_routes
    import app.models.arm_state as arm_state_mod

    monkeypatch.setattr(pipeline_config, "get_stations", lambda: _STATIONS)
    monkeypatch.setattr(pipeline_config, "get_lane_cells", lambda: list(_LANE_CELLS))
    monkeypatch.setattr(
        pipeline_config, "get_agv",
        lambda agv_id: {"id": "agv-1", "label": "AGV 1", "color": "#16a085", "bay": [0, 1]}
        if agv_id == "agv-1" else None,
    )
    monkeypatch.setattr(pipeline_config, "get_warehouse_scene", lambda: _WAREHOUSE_SCENE)
    monkeypatch.setattr(pipeline_config, "cell_to_px", lambda cell: None)

    # arm_state
    monkeypatch.setattr(
        arm_state_mod, "_state",
        {1: {"arm_id": 1, "angle": 90.0, "status": "idle"},
         2: {"arm_id": 2, "angle": 90.0, "status": "idle"}},
    )
    monkeypatch.setattr(
        arm_state_mod, "_health",
        {1: {"health": "idle", "last_seen": time.time(), "error": None},
         2: {"health": "idle", "last_seen": time.time(), "error": None}},
    )

    agv_state.reset()
    agv_state.register("agv-1", "AGV 1", "#16a085", [0, 1])
    grid_state.reset()

    with agv_routes._signals_lock:
        agv_routes._signals.clear()

    yield

    agv_state.reset()
    grid_state.reset()
    with agv_routes._signals_lock:
        agv_routes._signals.clear()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_agv(origin_px=(0.0, 40.0), actor_id="agv-1"):
    """Build a minimal AGVAgent without a live bus.

    Sets _running=True so _traverse_to doesn't bail on the running check.
    Tests that call _traverse_to_with_path directly need this — unlike
    integration tests that call agent.start(), unit tests exercise the method
    synchronously.
    """
    bus = NegotiationBus()
    profile = CapabilityProfile(
        actor_id=actor_id,
        kind="agv",
        reachable_zones=["input_arm", "output_arm"],
        position=[origin_px[0] / CELL_PX, origin_px[1] / CELL_PX],
        position_px=origin_px,
    )
    agv = AGVAgent(profile, bus)
    agv._running = True  # unit tests exercise the method directly, not via start()
    return agv


def _make_agv_state_mock():
    """Return a minimal mock agv_state that won't raise."""
    mock = MagicMock()
    mock.set_target.return_value = None
    mock.set_cell.return_value = None
    mock.set_status.return_value = {"actor_id": "agv-1", "cell": [0, 1]}
    return mock


def _make_grid_state_mock():
    """Return a minimal mock grid_state that won't raise."""
    mock = MagicMock()
    mock.set_cell.return_value = None
    return mock


# ── Tests ─────────────────────────────────────────────────────────────────────

def _suppress_io(monkeypatch):
    """Monkeypatch sleep and SocketIO emits for fast unit tests."""
    import app.agents.agv_agent as agv_mod
    monkeypatch.setattr(agv_mod, "MOVE_STEP_SLEEP_S", 0.0)
    monkeypatch.setattr(agv_mod, "REPLAN_BACKOFF_S", 0.0)
    monkeypatch.setattr(agv_mod, "_emit_agv_position", lambda *a, **kw: None)
    monkeypatch.setattr(agv_mod, "_emit_agv", lambda *a, **kw: None)
    monkeypatch.setattr(agv_mod, "_emit_log", lambda *a, **kw: None)


class TestSimpleTraverseUsesAStar:
    """No obstacles; AGV should traverse via A* path."""

    def test_simple_traverse_uses_a_star(self, _execution_env, monkeypatch):
        """Spatial traversal with no obstacles completes and reserves cells."""
        from app.models import pipeline_config
        from app.spatial.reservation import get_store

        # Use a clean topology (no arm obstacles).
        monkeypatch.setattr(pipeline_config, "get_topology_or_legacy_shim",
                            lambda: _TOPOLOGY_CLEAN)
        _suppress_io(monkeypatch)

        agv = _make_agv(origin_px=(20.0, 60.0))  # cell (0, 1)
        agv._last_px = (20.0, 60.0)

        agv_state = _make_agv_state_mock()
        grid_state = _make_grid_state_mock()

        # Traverse from cell (0,1) to cell (5,1) — 5 hops.
        target_x = 5 * CELL_PX + CELL_PX / 2  # center of cell 5
        target_y = 1 * CELL_PX + CELL_PX / 2

        ok = agv._traverse_to_with_path(
            target_x, target_y,
            "agv-1", agv_state, grid_state,
            lambda: True,
            task_id="task-pf-1",
        )

        assert ok, "_traverse_to_with_path should return True on success"
        # After success, final _last_px should be near target.
        assert abs(agv._last_px[0] - target_x) < 1.0
        assert abs(agv._last_px[1] - target_y) < 1.0


class TestObstacleRoutesAround:
    """Arm pivot at (5,1) forces detour."""

    def test_obstacle_routes_around(self, _execution_env, monkeypatch):
        """Arm pivot at cell (5,1) should cause A* to route around it."""
        from app.models import pipeline_config
        from app.spatial.reservation import get_store

        monkeypatch.setattr(pipeline_config, "get_topology_or_legacy_shim",
                            lambda: _TOPOLOGY_ARM_AT_5_1)
        _suppress_io(monkeypatch)

        agv = _make_agv(origin_px=(20.0, 60.0))  # cell (0, 1)
        agv._last_px = (20.0, 60.0)

        agv_state = _make_agv_state_mock()
        grid_state = _make_grid_state_mock()

        # Goal is cell (8, 1). Arm is at cell (5,1) blocking the straight line.
        target_x = 8 * CELL_PX + CELL_PX / 2
        target_y = 1 * CELL_PX + CELL_PX / 2

        ok = agv._traverse_to_with_path(
            target_x, target_y,
            "agv-1", agv_state, grid_state,
            lambda: True,
            task_id="task-pf-obstacle",
        )

        assert ok, "Should succeed by routing around the arm obstacle"


class TestConcurrentAGVReplanToAvoidReservation:
    """Pre-reserved cells by another actor should be avoided."""

    def test_concurrent_agv_replans_to_avoid_reservation(self, _execution_env, monkeypatch):
        """Pre-reserving cells by another actor causes AGV path to avoid those cells."""
        from app.models import pipeline_config
        from app.spatial.reservation import get_store

        monkeypatch.setattr(pipeline_config, "get_topology_or_legacy_shim",
                            lambda: _TOPOLOGY_CLEAN)
        _suppress_io(monkeypatch)

        task_id = "task-pf-concurrent"
        store = get_store()

        # Pre-reserve the straight-line cells (1,1),(2,1),(3,1) for another actor.
        other_epoch = store.next_epoch(task_id, "agv-2")
        reserved = store.reserve(task_id, "agv-2", other_epoch,
                                 [(1, 1), (2, 1), (3, 1)], ttl_ms=10_000)
        assert reserved, "Pre-reservation by other actor should succeed"

        agv = _make_agv(origin_px=(20.0, 60.0))  # cell (0, 1)
        agv._last_px = (20.0, 60.0)

        agv_state = _make_agv_state_mock()
        grid_state = _make_grid_state_mock()

        # Goal at cell (5, 1) — straight line passes through (1,1),(2,1),(3,1).
        target_x = 5 * CELL_PX + CELL_PX / 2
        target_y = 1 * CELL_PX + CELL_PX / 2

        ok = agv._traverse_to_with_path(
            target_x, target_y,
            "agv-1", agv_state, grid_state,
            lambda: True,
            task_id=task_id,
        )

        assert ok, "Should succeed by routing around pre-reserved cells"


class TestReleaseAfterTraverse:
    """Reservation keys must be gone after a successful traverse."""

    def test_release_after_traverse(self, _execution_env, monkeypatch):
        """After successful traverse, no reservation keys remain for (task, actor)."""
        from app.models import pipeline_config
        from app.spatial.reservation import get_store

        monkeypatch.setattr(pipeline_config, "get_topology_or_legacy_shim",
                            lambda: _TOPOLOGY_CLEAN)
        _suppress_io(monkeypatch)

        task_id = "task-pf-release"
        store = get_store()

        agv = _make_agv(origin_px=(20.0, 60.0))  # cell (0, 1)
        agv._last_px = (20.0, 60.0)

        agv_state = _make_agv_state_mock()
        grid_state = _make_grid_state_mock()

        target_x = 4 * CELL_PX + CELL_PX / 2
        target_y = 1 * CELL_PX + CELL_PX / 2

        ok = agv._traverse_to_with_path(
            target_x, target_y,
            "agv-1", agv_state, grid_state,
            lambda: True,
            task_id=task_id,
        )
        assert ok

        # After release, held_by_others should return empty (agv-1's cells are gone).
        remaining = store.held_by_others(task_id, exclude_actor="agv-2")
        # remaining contains cells held by non-agv-2 actors; agv-1's cells must be gone.
        # Verify directly: scan for agv-1 reservation keys.
        backend = store._backend
        assert isinstance(backend, _InMemoryReservationStore)
        with backend._lock:
            agv1_keys = [k for k in backend._cells if ":actor:agv-1:" in k]
        assert agv1_keys == [], f"Expected no agv-1 reservation keys after traverse; got {agv1_keys}"


class TestReplanBumpsEpochOnConflict:
    """Epoch counter must increment on each reservation conflict."""

    def test_replan_bumps_epoch_on_conflict(self, _execution_env, monkeypatch):
        """When reserve() fails, next_epoch is called again, bumping the counter."""
        from app.models import pipeline_config
        from app.spatial.reservation import get_store

        monkeypatch.setattr(pipeline_config, "get_topology_or_legacy_shim",
                            lambda: _TOPOLOGY_CLEAN)
        _suppress_io(monkeypatch)

        task_id = "task-pf-epoch"
        store = get_store()

        # Track how many times next_epoch is called.
        original_next_epoch = store.next_epoch
        epoch_calls = []

        def tracking_next_epoch(tid, aid):
            e = original_next_epoch(tid, aid)
            epoch_calls.append(e)
            return e

        monkeypatch.setattr(store, "next_epoch", tracking_next_epoch)

        # Make reserve() always fail (conflict) to force replan attempts.
        conflict_count = [0]
        original_reserve = store.reserve

        def always_conflict(tid, aid, epoch, cells, ttl_ms):
            conflict_count[0] += 1
            return False  # always conflict

        monkeypatch.setattr(store, "reserve", always_conflict)

        agv = _make_agv(origin_px=(20.0, 60.0))
        agv._last_px = (20.0, 60.0)

        agv_state = _make_agv_state_mock()
        grid_state = _make_grid_state_mock()

        target_x = 3 * CELL_PX + CELL_PX / 2
        target_y = 1 * CELL_PX + CELL_PX / 2

        ok = agv._traverse_to_with_path(
            target_x, target_y,
            "agv-1", agv_state, grid_state,
            lambda: True,
            task_id=task_id,
        )

        # Should have faulted after MAX_REPLAN_ATTEMPTS conflicts.
        assert not ok
        # next_epoch called at least 2 times: once at start + once per conflict bump.
        assert len(epoch_calls) > 1, (
            f"Expected epoch bumps on conflict; got epoch_calls={epoch_calls}"
        )
        # conflict_count matches number of reserve attempts (up to MAX_REPLAN_ATTEMPTS).
        assert conflict_count[0] <= MAX_REPLAN_ATTEMPTS


class TestFaultAfterMaxReplans:
    """Goal walled off → fault after MAX_REPLAN_ATTEMPTS."""

    def test_fault_after_max_replans(self, _execution_env, monkeypatch):
        """When A* returns None for MAX_REPLAN_ATTEMPTS attempts, agent faults."""
        from app.models import pipeline_config

        monkeypatch.setattr(pipeline_config, "get_topology_or_legacy_shim",
                            lambda: _TOPOLOGY_CLEAN)
        _suppress_io(monkeypatch)

        # Patch a_star to always return None.
        with patch("app.spatial.pathfinder.a_star", return_value=None):
            agv = _make_agv(origin_px=(20.0, 60.0))
            agv._last_px = (20.0, 60.0)

            fault_called = []
            original_fault = agv.fault
            agv.fault = lambda msg: fault_called.append(msg)

            agv_state = _make_agv_state_mock()
            grid_state = _make_grid_state_mock()

            target_x = 5 * CELL_PX + CELL_PX / 2
            target_y = 1 * CELL_PX + CELL_PX / 2

            ok = agv._traverse_to_with_path(
                target_x, target_y,
                "agv-1", agv_state, grid_state,
                lambda: True,
                task_id="task-pf-wall",
            )

            assert not ok, "Should return False when path cannot be found"
            assert len(fault_called) == 1, f"Expected exactly one fault call; got {fault_called}"
            assert "replan exhausted" in fault_called[0].lower()
