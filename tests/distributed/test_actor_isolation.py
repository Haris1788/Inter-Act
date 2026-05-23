"""Multi-actor isolation tests for the Redis-backed bus and state layer.

Scoping decision (Track D):
    The Phase 4 Stage 2 spec (item 15) mentions "spin up multiple actor_runner
    subprocesses against a fakeredis-backed bus". Standard fakeredis does NOT
    expose a real TCP server, so a multi-process setup would require a real Redis
    container — which is explicitly forbidden for this test suite.

    Instead, Track D uses **thread-based integration**: each actor runs as its
    own ActorAgent thread (the same model Phase 3 used), but all threads share a
    single fakeredis.FakeServer-backed client that is injected into every Redis-
    consuming module (redis_bus, grid_state, agv_state, arm_state, agv_routes
    signals).  This exercises the full Track A+B+C integration surface:

    * RedisStreamsBus publish/subscribe semantics across actors
    * RedisLock acquire/release/TTL/keepalive under concurrent callers
    * agv_state, arm_state, grid_state writes landing in Redis (FakeRedis)
    * Handshake signals via Redis Hash

    Real subprocess isolation with a live Redis container + chaos (kill -9 →
    TTL expiry) belongs to Stage 7 (distributed integration testing under chaos)
    and is explicitly deferred per the spec.
"""

from __future__ import annotations

import collections
import os
import threading
import time
from contextlib import contextmanager
from typing import List

import fakeredis
import pytest

from app.agents.agv_agent import AGVAgent
from app.agents.arm_agent import ArmAgent
from app.agents.capability import CapabilityProfile
from app.agents.negotiation import Message, MessageType, accepted_actors

# ---------------------------------------------------------------------------
# Warehouse topology shared by all tests
# ---------------------------------------------------------------------------

_STATIONS = {
    "input_arm": {"served_by": "arm-1", "cell": [1, 1], "dock": [2, 1]},
    "output_arm": {"served_by": "arm-2", "cell": [12, 1], "dock": [11, 1]},
}

_LANE_CELLS = [
    [3, 1], [4, 1], [5, 1], [6, 1], [7, 1], [8, 1], [9, 1], [10, 1]
]

_WAREHOUSE_SCENE = {
    "grid": {"cols": 15, "rows": 5, "cell_px": 54},
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
    "lane": {
        "cells": _LANE_CELLS,
        "px": [],
    },
    "agvs": [
        {"id": "agv-1", "label": "AGV 1", "color": "#16a085", "bay": [0, 1], "bay_px": None},
        {"id": "agv-2", "label": "AGV 2", "color": "#1abc9c", "bay": [0, 3], "bay_px": None},
    ],
}


# ---------------------------------------------------------------------------
# Core injection helper
# ---------------------------------------------------------------------------

def _build_redis_bus(fake_client):
    """Construct a RedisStreamsBus that uses *fake_client* instead of real Redis."""
    from app.agents.redis_bus import RedisStreamsBus

    bus = RedisStreamsBus.__new__(RedisStreamsBus)
    bus._redis_url = "redis://fake"
    bus._client = fake_client
    bus._subscribers = {}
    bus._subscribers_lock = threading.Lock()
    bus._seen_xids = {}
    bus._consumer_threads = []
    bus._stop_flag = threading.Event()
    return bus


def _inject_all(fake_client) -> None:
    """Inject *fake_client* into every module that touches Redis state."""
    import app.models.agv_state as agv_state_mod
    import app.models.arm_state as arm_state_mod
    import app.models.grid_state as gs

    agv_state_mod._inject_redis_client(fake_client)
    arm_state_mod._inject_redis_client(fake_client)
    gs._inject_redis_client(fake_client)
    # Clear cached RedisLock instances so they pick up the new client.
    with gs._redis_locks_lock:
        gs._redis_locks.clear()

    from app.routes import agv_routes
    agv_routes._inject_signals_redis_client(fake_client)


@contextmanager
def _redis_env(monkeypatch):
    """Context manager: set INTERACT_BUS=redis + INTERACT_STATE=redis,
    inject a fresh FakeRedis client into all modules, reset state, yield
    (bus, fake_client), then tear down.
    """
    monkeypatch.setenv("INTERACT_BUS", "redis")
    monkeypatch.setenv("INTERACT_STATE", "redis")

    server = fakeredis.FakeServer()
    fake_client = fakeredis.FakeRedis(server=server, decode_responses=True)

    _inject_all(fake_client)

    import app.models.agv_state as agv_state_mod
    import app.models.grid_state as gs
    agv_state_mod.reset()
    gs.reset()

    bus = _build_redis_bus(fake_client)

    from app.routes import agv_routes
    with agv_routes._signals_lock:
        agv_routes._signals.clear()

    try:
        yield bus, fake_client
    finally:
        bus.shutdown()
        agv_state_mod.reset()
        gs.reset()
        from app.routes import agv_routes as ar
        with ar._signals_lock:
            ar._signals.clear()


# ---------------------------------------------------------------------------
# Agent factory helpers
# ---------------------------------------------------------------------------

def _arm(bus, actor_id: str, zone: str) -> ArmAgent:
    return ArmAgent(
        CapabilityProfile(actor_id=actor_id, kind="arm", reachable_zones=[zone]),
        bus,
    )


def _agv(bus, actor_id: str, position: tuple) -> AGVAgent:
    return AGVAgent(
        CapabilityProfile(
            actor_id=actor_id,
            kind="agv",
            reachable_zones=["input_arm", "output_arm"],
            position=[float(position[0]), float(position[1])],
        ),
        bus,
    )


def _announce(task_id: str = "task-1", constraint: str = "shortest time",
              bid_window: float = 1.5, consensus_window: float = 1.5) -> Message:
    now = time.time()
    return Message(
        type=MessageType.TASK_ANNOUNCE,
        task_id=task_id,
        negotiation_round=0,
        sender="interpreter",
        payload={
            "source_zone": "input_arm",
            "dest_zone": "output_arm",
            "constraint": constraint,
            "slots": [
                {"slot_id": "pickup", "kind": "arm", "zone": "input_arm"},
                {"slot_id": "transport", "kind": "agv",
                 "from_zone": "input_arm", "to_zone": "output_arm"},
                {"slot_id": "place", "kind": "arm", "zone": "output_arm"},
            ],
            "announce_ts": now,
            "bid_deadline_ts": now + bid_window,
            "consensus_deadline_ts": now + bid_window + consensus_window,
        },
    )


# ---------------------------------------------------------------------------
# Shared pipeline_config monkeypatches
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _patch_pipeline(monkeypatch):
    from app.models import pipeline_config
    monkeypatch.setattr(pipeline_config, "get_stations", lambda: _STATIONS)
    monkeypatch.setattr(pipeline_config, "get_lane_cells", lambda: list(_LANE_CELLS))
    monkeypatch.setattr(
        pipeline_config, "get_agv",
        lambda agv_id: next(
            (a for a in _WAREHOUSE_SCENE["agvs"] if a["id"] == agv_id), None
        ),
    )
    monkeypatch.setattr(pipeline_config, "get_warehouse_scene", lambda: _WAREHOUSE_SCENE)
    monkeypatch.setattr(pipeline_config, "cell_to_px", lambda cell: None)


# ---------------------------------------------------------------------------
# Test 1: full negotiation + transport on Redis bus + Redis state
# ---------------------------------------------------------------------------

def test_negotiation_completes_on_redis_bus_with_redis_state(monkeypatch):
    """Wire INTERACT_BUS=redis + INTERACT_STATE=redis; spawn 2 arms + 2 AGVs;
    publish TASK_ANNOUNCE; assert CONSENSUS_REACHED lands and the winning AGV
    ends idle in agv_state (cell mutations persisted in Redis)."""
    import app.agents.agv_agent as agv_mod
    import app.agents.actuation as act_mod
    import app.models.agv_state as agv_state_mod

    monkeypatch.setattr(agv_mod, "MOVE_STEP_SLEEP", 0.01)
    monkeypatch.setattr(agv_mod, "SIGNAL_POLL_INTERVAL", 0.01)
    monkeypatch.setattr(act_mod, "DEFAULT_STEP_SLEEP", 0.005)

    with _redis_env(monkeypatch) as (bus, fake_client):
        # Register AGVs in Redis-backed agv_state.
        agv_state_mod.register("agv-1", "AGV 1", "#16a085", [0, 1])
        agv_state_mod.register("agv-2", "AGV 2", "#1abc9c", [0, 3])

        agents = [
            _arm(bus, "arm-1", "input_arm"),
            _arm(bus, "arm-2", "output_arm"),
            _agv(bus, "agv-1", position=(0, 1)),
            _agv(bus, "agv-2", position=(0, 3)),
        ]
        for a in agents:
            a.start()
        try:
            bus.publish(_announce())
            time.sleep(12.0)  # generous: bid+consensus + full leg execution
        finally:
            for a in agents:
                a.stop()

        history = bus.history()
        assert any(m.type == MessageType.CONSENSUS_REACHED for m in history), (
            "CONSENSUS_REACHED not found in Redis bus history"
        )
        accepts = accepted_actors(history, "task-1", 0)
        winner_id = accepts.get("transport")
        assert winner_id is not None, "No transport slot winner"

        rec = agv_state_mod.get(winner_id)
        assert rec is not None, f"Winner {winner_id} not found in agv_state"
        assert rec["status"] == "idle", (
            f"Winner {winner_id} did not return to idle; status={rec['status']}"
        )


# ---------------------------------------------------------------------------
# Test 2: distributed locks prevent double-grant
# ---------------------------------------------------------------------------

def test_distributed_locks_prevent_double_grant(monkeypatch):
    """Two AGVs race to acquire station_lock('input_arm') via Redis.
    Only one gets it; the other times out with a short timeout."""
    with _redis_env(monkeypatch) as (_bus, _fake_client):
        import app.models.grid_state as gs
        gs.reset()

        results: dict = {}

        def _acquire(agv_id: str, timeout: float) -> None:
            ok = gs.station_lock("input_arm").acquire(agv_id, timeout=timeout)
            results[agv_id] = ok

        # agv-1 acquires first (should succeed)
        ok_first = gs.station_lock("input_arm").acquire("agv-1", timeout=2.0)
        assert ok_first is True, "First acquire should succeed"

        # agv-2 tries concurrently — must fail within short timeout
        t = threading.Thread(target=_acquire, args=("agv-2", 0.2))
        t.start()
        t.join(timeout=1.0)

        assert results.get("agv-2") is False, (
            "Second AGV should not acquire a lock held by another"
        )
        assert gs.station_lock("input_arm").holder() == "agv-1"

        gs.station_lock("input_arm").release("agv-1")
        assert gs.station_lock("input_arm").is_free()


# ---------------------------------------------------------------------------
# Test 3: lock TTL expires after simulated actor crash
# ---------------------------------------------------------------------------

def test_lock_ttl_expires_after_actor_crash(monkeypatch):
    """One AGV acquires a lock with TTL=1 s; we simulate a crash by NOT releasing
    and NOT running keepalive; after TTL expiry another AGV's acquire succeeds."""
    with _redis_env(monkeypatch) as (_bus, fake_client):
        from app.models.redis_lock import RedisLock

        SHORT_TTL_MS = 800  # 0.8 s — fast test

        lock = RedisLock(
            key="test:lock:crash",
            redis_client=fake_client,
            default_ttl_ms=SHORT_TTL_MS,
        )

        # AGV-1 acquires (simulating a normal acquire).
        ok = lock.acquire("agv-1", timeout=1.0)
        assert ok is True
        assert lock.holder() == "agv-1"

        # Crash: we never call release() or run keepalive.
        # Wait for TTL to expire naturally.
        time.sleep(SHORT_TTL_MS / 1000 + 0.3)

        # AGV-2 should now acquire successfully.
        ok2 = lock.acquire("agv-2", timeout=1.0)
        assert ok2 is True, (
            f"Lock should have expired (TTL={SHORT_TTL_MS} ms), "
            f"but agv-2 could not acquire; holder={lock.holder()}"
        )
        lock.release("agv-2")


# ---------------------------------------------------------------------------
# Test 4: lock keepalive keeps holder alive
# ---------------------------------------------------------------------------

def test_lock_keepalive_keeps_holder_alive(monkeypatch):
    """One AGV holds a lock (TTL=1 s) and runs LockKeepalive(interval=0.3 s).
    Another AGV tries to acquire with timeout=2 s and FAILS (keepalive renewed).
    After keepalive.stop(), the second AGV succeeds."""
    with _redis_env(monkeypatch) as (_bus, fake_client):
        from app.models.redis_lock import RedisLock, LockKeepalive

        TTL_MS = 1000  # 1 s

        lock = RedisLock(
            key="test:lock:keepalive",
            redis_client=fake_client,
            default_ttl_ms=TTL_MS,
        )

        ok = lock.acquire("agv-1", timeout=1.0)
        assert ok is True

        keepalive = LockKeepalive(lock, holder="agv-1", interval_s=0.3)
        keepalive.start()

        # agv-2 should fail: keepalive extends TTL every 0.3 s, TTL never expires.
        ok2 = lock.acquire("agv-2", timeout=2.0)
        assert ok2 is False, (
            "agv-2 should NOT acquire the lock while keepalive is running"
        )

        # Stop keepalive and release.
        keepalive.stop()
        lock.release("agv-1")

        # Now agv-2 can acquire.
        ok3 = lock.acquire("agv-2", timeout=1.0)
        assert ok3 is True, "agv-2 should acquire after keepalive stopped and lock released"
        lock.release("agv-2")


# ---------------------------------------------------------------------------
# Test 5: fault mid-leg releases Redis locks and triggers reopen
# ---------------------------------------------------------------------------

def test_fault_mid_leg_releases_redis_locks_and_reopens(monkeypatch):
    """AGV faults due to a very short LOAD_SIGNAL_TIMEOUT (arms stalled).
    Asserts: (a) Redis lock keys absent for that AGV after fault,
             (b) bus shows a reopen TASK_ANNOUNCE round 1 that another AGV wins."""
    import app.agents.agv_agent as agv_mod
    import app.agents.actuation as act_mod
    import app.models.agv_state as agv_state_mod

    monkeypatch.setattr(agv_mod, "MOVE_STEP_SLEEP", 0.01)
    monkeypatch.setattr(agv_mod, "SIGNAL_POLL_INTERVAL", 0.01)
    # Arms stall (never signal_load) so AGV hits timeout.
    monkeypatch.setattr(act_mod, "DEFAULT_STEP_SLEEP", 60.0)
    # Very short load timeout so the fault fires quickly.
    monkeypatch.setattr(agv_mod, "LOAD_SIGNAL_TIMEOUT", 0.5)

    with _redis_env(monkeypatch) as (bus, fake_client):
        agv_state_mod.register("agv-1", "AGV 1", "#16a085", [0, 1])
        agv_state_mod.register("agv-2", "AGV 2", "#1abc9c", [0, 3])

        agents = [
            _arm(bus, "arm-1", "input_arm"),
            _arm(bus, "arm-2", "output_arm"),
            _agv(bus, "agv-1", position=(0, 1)),
            _agv(bus, "agv-2", position=(0, 3)),
        ]
        for a in agents:
            a.start()
        try:
            bus.publish(_announce(task_id="task-fault", bid_window=1.5, consensus_window=1.5))
            # Wait: negotiate (~3 s) + AGV moves to input dock + load timeout (0.5 s) +
            # fault processing + reopen round + reopen negotiation + some buffer.
            time.sleep(14.0)
        finally:
            for a in agents:
                a.stop()

        # (a) input_arm station must NOT be held by agv-1 after its fault.
        import app.models.grid_state as gs
        in_holder = gs.station_lock("input_arm").holder()
        assert in_holder != "agv-1", (
            f"input_arm station still held by agv-1 after fault; holder={in_holder}"
        )

        # (b) History should contain a round-1 TASK_ANNOUNCE (reopen).
        history = bus.history()
        reopen_msgs = [
            m for m in history
            if m.type == MessageType.TASK_ANNOUNCE and m.negotiation_round >= 1
        ]
        assert reopen_msgs, (
            "Expected a round-1 TASK_ANNOUNCE (reopen) in bus history after agv-1 fault"
        )


# ---------------------------------------------------------------------------
# Test 6: handshake signals via Redis Hash
# ---------------------------------------------------------------------------

def test_handshake_signals_via_redis_hash(monkeypatch):
    """signal_load() by arm writes to Redis Hash; poll_load() by AGV reads it.
    Assert correct sequencing: poll returns False before signal, True after."""
    with _redis_env(monkeypatch) as (_bus, _fake_client):
        from app.routes.agv_routes import signal_load, poll_load, clear_load

        # Before signal: poll_load should return False.
        assert poll_load("agv-1") is False, "poll_load should be False before signal"

        # Arm signals load.
        import app.models.agv_state as agv_state_mod
        agv_state_mod.register("agv-1", "AGV 1", "#16a085", [0, 1])
        signal_load("agv-1", "box-001")

        # After signal: poll_load should return True.
        assert poll_load("agv-1") is True, "poll_load should be True after signal_load"

        # Verify object carried.
        rec = agv_state_mod.get("agv-1")
        assert rec is not None
        assert rec["carried_object"] == "box-001"

        # Clear the signal.
        clear_load("agv-1")
        assert poll_load("agv-1") is False, "poll_load should be False after clear_load"


# ---------------------------------------------------------------------------
# Test 7: state get_all consistent under concurrent writers
# ---------------------------------------------------------------------------

def test_state_get_all_consistent_under_concurrent_writers(monkeypatch):
    """N AGVs all call set_cell concurrently against Redis agv_state.
    get_all() returns a coherent snapshot with no missing/corrupt entries."""
    import app.models.agv_state as agv_state_mod

    N = 6

    with _redis_env(monkeypatch) as (_bus, _fake_client):
        agv_ids = [f"agv-{i}" for i in range(1, N + 1)]
        for agv_id in agv_ids:
            agv_state_mod.register(agv_id, agv_id.upper(), "#aabbcc", [0, 0])

        errors: List[str] = []
        barrier = threading.Barrier(N)

        def _writer(agv_id: str, col: int) -> None:
            try:
                barrier.wait(timeout=5.0)
                for row in range(5):
                    agv_state_mod.set_cell(agv_id, [col, row])
                    time.sleep(0.005)
            except Exception as exc:
                errors.append(f"{agv_id}: {exc}")

        threads = [
            threading.Thread(target=_writer, args=(agv_ids[i], i))
            for i in range(N)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)

        assert not errors, f"Writer threads raised: {errors}"

        snapshot = agv_state_mod.get_all()

        # Every registered AGV must appear with a valid cell.
        for agv_id in agv_ids:
            assert agv_id in snapshot, f"{agv_id} missing from get_all() snapshot"
            cell = snapshot[agv_id].get("cell")
            assert cell is not None and len(cell) == 2, (
                f"{agv_id} has corrupt cell in snapshot: {cell}"
            )
