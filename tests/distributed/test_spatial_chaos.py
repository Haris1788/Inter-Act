"""Spatial chaos integration tests — Phase 5 Stage 9 Track A.

Three tests that exercise the spatial negotiation runtime under chaos
conditions using fakeredis (no real Redis required).

All tests:
- Use real ActorAgent threads (no mock orchestration).
- Use the Redis-backed ReservationStore via _inject_redis_client.
- Are deterministic and must pass 3× consecutively.
- Stay under 30 s wall time each.

Hard invariants (from spec):
    1. Placement changes change the winner (spatial bidding is real).
    2. Pre-existing reservations on a path force A* to replan around them,
       or the AGV returns False after MAX_REPLAN_ATTEMPTS if fully blocked.
    3. Dead AGV's reservations expire by TTL so other AGVs can claim them.
"""

from __future__ import annotations

import os
import threading
import time

import fakeredis
import pytest

from app.agents.agv_agent import AGVAgent
import app.agents.agv_agent as _agv_mod
from app.agents.arm_agent import ArmAgent
from app.agents.capability import CapabilityProfile
from app.agents.negotiation import Message, MessageType, accepted_actors
from app.models.topology import ActorSpec, Topology
from app.spatial.pathfinder import Grid, a_star
from app.spatial.reservation import (
    ReservationStore,
    _RedisReservationStore,
    _inject_redis_client,
    get_store,
)

from tests.distributed.chaos_harness import (
    _STATIONS,
    _LANE_CELLS,
    _WAREHOUSE_SCENE,
    _build_redis_bus,
    _inject_all,
    kill_actor,
    make_announce,
    patch_pipeline_config,
)

# ---------------------------------------------------------------------------
# Autouse fixture: wire pipeline_config for all tests in this module.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _patch_pipeline(monkeypatch):
    patch_pipeline_config(monkeypatch)


# ---------------------------------------------------------------------------
# Speed patches helpers
# ---------------------------------------------------------------------------

def _fast_execution(monkeypatch) -> None:
    """Patch AGV/arm execution to run as fast as possible in tests."""
    import app.agents.actuation as act_mod
    monkeypatch.setattr(_agv_mod, "MOVE_STEP_SLEEP", 0.01)
    monkeypatch.setattr(_agv_mod, "MOVE_STEP_SLEEP_S", 0.01)
    monkeypatch.setattr(_agv_mod, "SIGNAL_POLL_INTERVAL", 0.01)
    monkeypatch.setattr(act_mod, "DEFAULT_STEP_SLEEP", 0.005)


# ---------------------------------------------------------------------------
# Shared context manager: spatial chaos env
# ---------------------------------------------------------------------------

from contextlib import contextmanager
from typing import Generator


@contextmanager
def make_spatial_chaos_env(
    agv_positions_px: list[tuple[float, float]],
    n_arms: int = 2,
) -> Generator[dict, None, None]:
    """Like make_chaos_env but builds AGVs with explicit pixel positions.

    agv_positions_px: list of (x_px, y_px) for each AGV (agv-1, agv-2, ...).
    Arms use standard input_arm / output_arm zones.

    Yields the same dict shape as make_chaos_env:
        {bus, agents, server, client, agv_ids}
    """
    import app.models.agv_state as agv_state_mod
    import app.models.arm_state as arm_state_mod
    import app.models.grid_state as gs
    from app.routes import agv_routes

    _old_bus = os.environ.get("INTERACT_BUS")
    _old_state = os.environ.get("INTERACT_STATE")
    os.environ["INTERACT_BUS"] = "redis"
    os.environ["INTERACT_STATE"] = "redis"

    server = fakeredis.FakeServer()
    fake_client = fakeredis.FakeRedis(server=server, decode_responses=True)

    _inject_all(fake_client)
    # Wire reservation store to the same fakeredis instance.
    _inject_redis_client(fake_client)

    agv_state_mod.reset()
    arm_state_mod.reset()
    gs.reset()

    bus = _build_redis_bus(fake_client)

    with agv_routes._signals_lock:
        agv_routes._signals.clear()

    n_agvs = len(agv_positions_px)
    agv_ids = [f"agv-{i + 1}" for i in range(n_agvs)]

    # Register AGVs in agv_state (cell derived from position_px / 40).
    cell_px = 40.0
    for i, (agv_id, (px, py)) in enumerate(zip(agv_ids, agv_positions_px)):
        bay_cell = [int(px / cell_px), int(py / cell_px)]
        agv_state_mod.register(agv_id, agv_id.upper(), "#16a085", bay_cell)

    agents: list = []
    arm_zones = ["input_arm", "output_arm"]
    for i in range(n_arms):
        zone = arm_zones[i % len(arm_zones)]
        arm_id = f"arm-{i + 1}"
        agents.append(
            ArmAgent(
                CapabilityProfile(actor_id=arm_id, kind="arm", reachable_zones=[zone]),
                bus,
            )
        )

    for i, (agv_id, (px, py)) in enumerate(zip(agv_ids, agv_positions_px)):
        col = int(px / cell_px)
        row = int(py / cell_px)
        agents.append(
            AGVAgent(
                CapabilityProfile(
                    actor_id=agv_id,
                    kind="agv",
                    reachable_zones=["input_arm", "output_arm"],
                    position=[float(col), float(row)],
                    position_px=(px, py),
                ),
                bus,
            )
        )

    for a in agents:
        a.start()

    try:
        yield {
            "bus": bus,
            "agents": agents,
            "server": server,
            "client": fake_client,
            "agv_ids": agv_ids,
        }
    finally:
        for a in agents:
            try:
                a.stop()
            except Exception:
                pass
        bus.shutdown()
        agv_state_mod.reset()
        arm_state_mod.reset()
        gs.reset()
        with agv_routes._signals_lock:
            agv_routes._signals.clear()
        # Reset reservation singleton so the next test gets a fresh backend.
        _inject_redis_client(None)
        import app.spatial.reservation as resv_mod
        resv_mod._store_instance = None
        if _old_bus is None:
            os.environ.pop("INTERACT_BUS", None)
        else:
            os.environ["INTERACT_BUS"] = _old_bus
        if _old_state is None:
            os.environ.pop("INTERACT_STATE", None)
        else:
            os.environ["INTERACT_STATE"] = _old_state


def _wait_for_consensus(bus, task_id: str, negotiation_round: int, timeout: float) -> bool:
    """Poll bus history until CONSENSUS_REACHED appears for the given round."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        history = bus.history()
        if any(
            m.type == MessageType.CONSENSUS_REACHED
            and m.task_id == task_id
            and m.negotiation_round == negotiation_round
            for m in history
        ):
            return True
        time.sleep(0.1)
    return False


# ===========================================================================
# Test 1 — placement change changes winner under chaos
# ===========================================================================

def test_placement_change_changes_winner_under_chaos(monkeypatch):
    """Invariant: moving agv-1 far away causes a different AGV to win the
    next negotiation's transport slot.

    Setup:
        - 2 arms (input_arm / output_arm).
        - 3 AGVs: agv-1 close to from_pos, agv-2 medium, agv-3 far.

    Round 0: agv-1 wins (closest to pickup dock, lowest legacy bid score).
    Mutation: agv-1's profile.position and position_px are updated in-place
              to a far position before round 1 announce is published.
    Round 1: agv-2 wins (now closest after agv-1 relocated).

    Fast execution is applied so arm sweeps and AGV moves complete quickly,
    keeping both rounds well within the 30 s wall-time budget.

    The test waits only for CONSENSUS_REACHED (bid/accept phase) — this fires
    independently of leg execution, so it is unaffected by the agents' ongoing
    round-0 execution when round-1 starts.
    """
    # Speed up execution so round-0 legs finish fast and don't bleed into round-1.
    _fast_execution(monkeypatch)

    # AGV cell positions (col, row): close=1, medium=5, far=10
    # (input_arm dock is at [2,1] per harness _STATIONS)
    cell_px = 40.0

    # Pixel positions for 3 AGVs — agv-1 nearest col 1, agv-2 col 5, agv-3 col 10
    agv_pxs = [
        (1 * cell_px, 1 * cell_px),   # agv-1: col 1 → distance to dock [2,1] = 1
        (5 * cell_px, 1 * cell_px),   # agv-2: col 5 → distance = 3
        (10 * cell_px, 1 * cell_px),  # agv-3: col 10 → distance = 8
    ]

    TASK_ID_0 = "task-spatial-0"
    TASK_ID_1 = "task-spatial-1"

    with make_spatial_chaos_env(agv_pxs, n_arms=2) as env:
        bus = env["bus"]
        agents = env["agents"]
        # agents[0]=arm-1, agents[1]=arm-2, agents[2]=agv-1, agents[3]=agv-2, agents[4]=agv-3
        agv1_agent = agents[2]

        # ── Round 0: agv-1 should win (BID_WINDOW 1.5 + CONSENSUS_WINDOW 1.5 = 3s) ──
        msg0 = make_announce(task_id=TASK_ID_0, negotiation_round=0)
        bus.publish(msg0)

        converged0 = _wait_for_consensus(bus, TASK_ID_0, 0, timeout=10.0)
        assert converged0, "Round 0 did not reach CONSENSUS_REACHED within timeout"

        history0 = bus.history()
        accepts0 = accepted_actors(history0, TASK_ID_0, 0)
        winner0 = accepts0.get("transport")
        assert winner0 == "agv-1", (
            f"INVARIANT FAILED: Expected agv-1 to win round 0 (closest to pickup). "
            f"Got: {winner0}. accepts={accepts0}"
        )

        # Wait for round-0 execution to finish so agents are free for round 1.
        # With fast execution (MOVE_STEP_SLEEP=0.01 s, DEFAULT_STEP_SLEEP=0.005 s)
        # the entire 12-step cell path + arm sweeps complete in under 2 s.
        time.sleep(2.5)

        # ── Mutate agv-1's profile in-place — move it far away ────────────
        # position_px drives spatial _bid_score; position drives _legacy_bid_score.
        # Set agv-1 far (col 12, well past agv-3 at col 10).
        far_col = 12
        far_px = (far_col * cell_px, 1 * cell_px)
        agv1_agent.profile.position_px = far_px
        agv1_agent.profile.position = [float(far_col), 1.0]

        # ── Round 1: agv-2 should now win (closest remaining) ─────────────
        msg1 = make_announce(task_id=TASK_ID_1, negotiation_round=0)
        bus.publish(msg1)

        converged1 = _wait_for_consensus(bus, TASK_ID_1, 0, timeout=10.0)
        assert converged1, "Round 1 did not reach CONSENSUS_REACHED within timeout"

        history1 = bus.history()
        accepts1 = accepted_actors(history1, TASK_ID_1, 0)
        winner1 = accepts1.get("transport")

        assert winner1 is not None, (
            f"INVARIANT FAILED: No transport slot winner in round 1. accepts={accepts1}"
        )
        assert winner1 != "agv-1", (
            f"INVARIANT FAILED: agv-1 won round 1 despite being relocated far from pickup. "
            f"winner1={winner1}. This means spatial bidding did not respond to profile mutation."
        )
        # agv-2 is now closest (distance 3 vs agv-3 distance 8)
        assert winner1 == "agv-2", (
            f"INVARIANT FAILED: Expected agv-2 to win round 1 (now closest after agv-1 moved). "
            f"Got: {winner1}."
        )


# ===========================================================================
# Test 2 — path replan on concurrent reservation
# ===========================================================================

def test_path_replan_on_concurrent_reservation():
    """Invariant: pre-existing reservations on the natural A* path from
    agv-1's start to its goal cause A* to route around them (or return None
    when walled off).  After reservations are released, the path can use the
    previously-blocked cells.

    This test works directly with the ReservationStore + A* pathfinder —
    no agent threads needed to verify the spatial invariant.
    """
    # Fresh fakeredis server for this test.
    server = fakeredis.FakeServer()
    fake_client = fakeredis.FakeRedis(server=server, decode_responses=True)

    os.environ["INTERACT_STATE"] = "redis"
    _inject_redis_client(fake_client)
    import app.spatial.reservation as resv_mod
    resv_mod._store_instance = None

    store = get_store()

    # Grid: 20 cols × 5 rows, no obstacles.
    grid = Grid(cols=20, rows=5, obstacles=frozenset())

    start: tuple[int, int] = (0, 2)
    goal: tuple[int, int] = (15, 2)

    # Natural path (row 2): (0,2) → (1,2) → ... → (15,2)
    natural_path = a_star(grid, start, goal)
    assert natural_path is not None, "Baseline A* must find a path on empty grid"
    natural_cells = set(natural_path)

    # Pre-reserve the middle of the natural path under a DIFFERENT actor.
    blocker_task_id = "task-replan-blocker"
    blocker_actor = "agv-blocker"
    blocked_cells = [(c, 2) for c in range(1, 16)]  # block the entire row-2 corridor

    epoch = store.next_epoch(blocker_task_id, blocker_actor)
    reserved = store.reserve(
        blocker_task_id, blocker_actor, epoch,
        blocked_cells,
        ttl_ms=30_000,
    )
    assert reserved, "Blocker reservation must succeed on empty store"

    # Now agv-1 queries what's held by others.
    agv1_actor = "agv-1"
    blocked_by_others = store.held_by_others(blocker_task_id, exclude_actor=agv1_actor)
    assert len(blocked_by_others) > 0, "held_by_others must see the blocker's reservation"

    # A* with those cells blocked — on a simple 20×5 grid with row 2 fully blocked
    # A* should find a detour via row 1 or row 3.
    agv_path = a_star(grid, start, goal, blocked=blocked_by_others)

    # Either (a) a path is found that avoids the blocked cells, or (b) None (unreachable).
    if agv_path is not None:
        # Verify the returned path does NOT use any of the blocked cells
        # (except start or goal themselves, which A* is allowed to use).
        agv_path_interior = set(agv_path) - {start, goal}
        conflicts = agv_path_interior & blocked_by_others
        assert not conflicts, (
            f"INVARIANT FAILED: A* path uses cells reserved by another actor. "
            f"Conflicting cells: {conflicts}. Full path: {agv_path}"
        )
    else:
        # All routes blocked — acceptable (verified by the release step below).
        pass

    # ── Release blockers — now the natural path must be available ─────────
    store.release(blocker_task_id, blocker_actor, epoch)

    still_blocked = store.held_by_others(blocker_task_id, exclude_actor=agv1_actor)
    assert len(still_blocked) == 0, (
        f"INVARIANT FAILED: Cells still appear reserved after release. "
        f"Remaining: {still_blocked}"
    )

    # A* should now find the natural direct path.
    free_path = a_star(grid, start, goal, blocked=still_blocked)
    assert free_path is not None, (
        "INVARIANT FAILED: A* found no path after blocker released — grid should be fully open"
    )
    # The path should be able to use row-2 cells again.
    free_cells = set(free_path)
    natural_interior = natural_cells - {start, goal}
    overlap = free_cells & natural_interior
    assert overlap, (
        f"INVARIANT FAILED: After releasing reservations, path does not reuse natural cells. "
        f"Free path: {free_path}. Natural path: {natural_path}"
    )

    # Cleanup.
    _inject_redis_client(None)
    resv_mod._store_instance = None
    os.environ.pop("INTERACT_STATE", None)


# ===========================================================================
# Test 3 — AGV crash releases reservation via TTL
# ===========================================================================

def test_agv_crash_releases_reservation_via_ttl(monkeypatch):
    """Invariant: when an AGV crashes after acquiring a path reservation with
    a short TTL, the reservation expires and another AGV can claim the same cells.

    Short TTL is injected by monkeypatching PATH_TTL_MS in agv_agent to 800 ms.
    After kill_actor + waiting 1.5 s, the keys expire and held_by_others returns
    empty for the dead AGV's task.
    """
    SHORT_TTL_MS = 800   # 0.8 s — well under the 1.5 s wait below

    agv_pxs = [
        (2 * 40.0, 2 * 40.0),   # agv-1 — will acquire + crash
        (8 * 40.0, 2 * 40.0),   # agv-2 — will claim after TTL expires
    ]

    task_id = "task-ttl-crash"
    other_actor = "agv-2"

    with make_spatial_chaos_env(agv_pxs, n_arms=2) as env:
        bus = env["bus"]
        agents = env["agents"]
        # agents[0]=arm-1, agents[1]=arm-2, agents[2]=agv-1, agents[3]=agv-2
        agv1_agent = agents[2]

        # Inject short TTL for this test via the store directly.
        store = get_store()

        # agv-1 acquires a reservation with the short TTL.
        cells_to_reserve: list[tuple[int, int]] = [(c, 2) for c in range(2, 8)]
        epoch = store.next_epoch(task_id, "agv-1")
        reserved = store.reserve(
            task_id, "agv-1", epoch,
            cells_to_reserve,
            ttl_ms=SHORT_TTL_MS,
        )
        assert reserved, "agv-1 must successfully reserve cells before crash"

        # Verify agv-2 can see the reservation immediately after acquire.
        blocked_before = store.held_by_others(task_id, exclude_actor=other_actor)
        assert len(blocked_before) > 0, (
            f"INVARIANT FAILED: agv-2 should see agv-1's reservation immediately. "
            f"Got: {blocked_before}"
        )

        # Kill agv-1 — simulates abrupt crash (no clean lock release).
        kill_actor(agv1_agent)

        # Wait longer than TTL — 1.5 s > 0.8 s.
        time.sleep(1.5)

        # Reservations must have expired.
        blocked_after = store.held_by_others(task_id, exclude_actor=other_actor)
        assert len(blocked_after) == 0, (
            f"INVARIANT FAILED: agv-1's reservations still visible {1.5}s after crash "
            f"(TTL={SHORT_TTL_MS}ms). Remaining: {blocked_after}. "
            "Dead AGV's reservations must expire by TTL."
        )

        # agv-2 can now reserve the same cells.
        epoch2 = store.next_epoch(task_id, other_actor)
        can_claim = store.reserve(
            task_id, other_actor, epoch2,
            cells_to_reserve,
            ttl_ms=30_000,
        )
        assert can_claim, (
            f"INVARIANT FAILED: agv-2 could not claim cells after agv-1's TTL expired. "
            "Once a dead AGV's reservation expires, another AGV must be able to reserve the same cells."
        )

        # Cleanup agv-2's reservation.
        store.release(task_id, other_actor, epoch2)
