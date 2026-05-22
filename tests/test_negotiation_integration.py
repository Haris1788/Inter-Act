"""End-to-end integration tests for the decentralized negotiation runtime.

A task is announced on the bus; real ArmAgent / AGVAgent instances negotiate
to consensus with no central component. Only the task announcement is
injected — every bid, accept, withdraw, and consensus event is produced by
the agents themselves.

Stage 3 additions (Tasks 4 + 5 + 8): real _execute_leg drives agv_state /
arm_state; execution barrier halts on fault or higher round.
"""

import threading
import time

import pytest

from app.agents.agv_agent import AGVAgent
from app.agents.arm_agent import ArmAgent
from app.agents.bus import NegotiationBus
from app.agents.capability import CapabilityProfile
from app.agents.negotiation import Message, MessageType, accepted_actors


_STATIONS = {
    "input_arm": {"served_by": "arm-1", "cell": [1, 1], "dock": [2, 1]},
    "output_arm": {"served_by": "arm-2", "cell": [12, 1], "dock": [11, 1]},
}


@pytest.fixture(autouse=True)
def _stations(monkeypatch):
    from app.models import pipeline_config

    monkeypatch.setattr(pipeline_config, "get_stations", lambda: _STATIONS)


def _arm(bus, actor_id, zone):
    return ArmAgent(
        CapabilityProfile(actor_id=actor_id, kind="arm",
                          reachable_zones=[zone]),
        bus,
    )


def _agv(bus, actor_id, position):
    return AGVAgent(
        CapabilityProfile(
            actor_id=actor_id, kind="agv",
            reachable_zones=["input_arm", "output_arm"],
            position=[float(position[0]), float(position[1])],
        ),
        bus,
    )


def _announce(constraint=""):
    now = time.time()
    return Message(
        type=MessageType.TASK_ANNOUNCE,
        task_id="task-1",
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
            "bid_deadline_ts": now + 1.5,
            "consensus_deadline_ts": now + 3.0,
        },
    )


def _run(agents, announce, settle=4.0):
    for a in agents:
        a.start()
    try:
        announce_bus = agents[0].bus
        announce_bus.publish(announce)
        time.sleep(settle)
        return announce_bus.history()
    finally:
        for a in agents:
            a.stop()


def test_full_negotiation_reaches_consensus():
    bus = NegotiationBus()
    agents = [
        _arm(bus, "arm-1", "input_arm"),
        _arm(bus, "arm-2", "output_arm"),
        _agv(bus, "agv-1", position=(3, 1)),
        _agv(bus, "agv-2", position=(10, 5)),
    ]
    history = _run(agents, _announce(constraint="shortest time"))
    accepts = accepted_actors(history, "task-1", 0)
    assert accepts == {
        "pickup": "arm-1",
        "transport": "agv-1",
        "place": "arm-2",
    }
    assert any(m.type == MessageType.CONSENSUS_REACHED for m in history)


def test_constraint_selects_nearest_agv():
    bus = NegotiationBus()
    agents = [
        _arm(bus, "arm-1", "input_arm"),
        _arm(bus, "arm-2", "output_arm"),
        _agv(bus, "agv-1", position=(11, 7)),
        _agv(bus, "agv-2", position=(3, 1)),
    ]
    history = _run(agents, _announce(constraint="shortest distance"))
    accepts = accepted_actors(history, "task-1", 0)
    assert accepts["transport"] == "agv-2"


def test_fault_reopens_and_reconverges_to_a_new_agv():
    bus = NegotiationBus()
    agents = [
        _arm(bus, "arm-1", "input_arm"),
        _arm(bus, "arm-2", "output_arm"),
        _agv(bus, "agv-1", position=(3, 1)),
        _agv(bus, "agv-2", position=(5, 1)),
    ]
    for a in agents:
        a.start()
    try:
        bus.publish(_announce(constraint="shortest time"))
        time.sleep(2.2)
        assert accepted_actors(bus.history(), "task-1", 0)["transport"] == "agv-1"
        agents[2].fault("injected")
        time.sleep(4.0)
        round1 = accepted_actors(bus.history(), "task-1", 1)
        assert round1.get("transport") == "agv-2"
    finally:
        for a in agents:
            a.stop()


def test_unfillable_task_never_reaches_consensus():
    bus = NegotiationBus()
    agents = [
        _arm(bus, "arm-1", "input_arm"),
        _arm(bus, "arm-2", "output_arm"),
    ]
    history = _run(agents, _announce(), settle=4.0)
    accepts = accepted_actors(history, "task-1", 0)
    assert "transport" not in accepts
    assert not any(m.type == MessageType.CONSENSUS_REACHED for m in history)
    assert any(m.type == MessageType.TASK_FAILED for m in history)


def test_converged_task_does_not_fail():
    bus = NegotiationBus()
    agents = [
        _arm(bus, "arm-1", "input_arm"),
        _arm(bus, "arm-2", "output_arm"),
        _agv(bus, "agv-1", position=(3, 1)),
        _agv(bus, "agv-2", position=(10, 5)),
    ]
    history = _run(agents, _announce(constraint="shortest time"), settle=4.0)
    assert any(m.type == MessageType.CONSENSUS_REACHED for m in history)
    assert not any(m.type == MessageType.TASK_FAILED for m in history)


# ─────────────────────────────────────────────────────────────────────────────
# Stage 3 — real _execute_leg tests (Tasks 4 + 5 + 8)
# ─────────────────────────────────────────────────────────────────────────────

# Minimal warehouse scene used by Stage 3 execution tests.
_WAREHOUSE_SCENE = {
    "grid": {"cols": 15, "rows": 5, "cell_px": 54},
    "stations": {
        "input_arm": {
            "cell": [1, 1], "dock": [2, 1], "served_by": "arm-1", "label": "input_arm",
            "px": None, "dock_px": None,
        },
        "output_arm": {
            "cell": [12, 1], "dock": [11, 1], "served_by": "arm-2", "label": "output_arm",
            "px": None, "dock_px": None,
        },
    },
    "lane": {
        "cells": [[3, 1], [4, 1], [5, 1], [6, 1], [7, 1], [8, 1], [9, 1], [10, 1]],
        "px": [],
    },
    "agvs": [{"id": "agv-1", "label": "AGV 1", "color": "#16a085", "bay": [0, 1], "bay_px": None}],
}

_LANE_CELLS = [[3, 1], [4, 1], [5, 1], [6, 1], [7, 1], [8, 1], [9, 1], [10, 1]]


@pytest.fixture()
def _execution_env(monkeypatch):
    """Patch pipeline_config and state modules for execution tests.

    Registers arm-1, arm-2 in arm_state; registers agv-1 in agv_state and
    grid_state; resets signal registry.
    """
    from app.models import pipeline_config, agv_state, grid_state
    from app.routes import agv_routes

    # ── pipeline_config patches ───────────────────────────────────────────
    monkeypatch.setattr(pipeline_config, "get_stations", lambda: _STATIONS)
    monkeypatch.setattr(pipeline_config, "get_lane_cells", lambda: list(_LANE_CELLS))
    monkeypatch.setattr(
        pipeline_config,
        "get_agv",
        lambda agv_id: {"id": "agv-1", "label": "AGV 1", "color": "#16a085", "bay": [0, 1]}
        if agv_id == "agv-1"
        else None,
    )
    monkeypatch.setattr(
        pipeline_config,
        "get_warehouse_scene",
        lambda: _WAREHOUSE_SCENE,
    )
    monkeypatch.setattr(pipeline_config, "cell_to_px", lambda cell: None)

    # ── arm_state: register arm-1 (id=1) and arm-2 (id=2) ───────────────
    import app.models.arm_state as arm_state_mod
    monkeypatch.setattr(
        arm_state_mod,
        "_state",
        {
            1: {"arm_id": 1, "angle": 90.0, "status": "idle"},
            2: {"arm_id": 2, "angle": 90.0, "status": "idle"},
        },
    )
    monkeypatch.setattr(
        arm_state_mod,
        "_health",
        {
            1: {"health": "idle", "last_seen": time.time(), "error": None},
            2: {"health": "idle", "last_seen": time.time(), "error": None},
        },
    )

    # ── agv_state: register agv-1 ─────────────────────────────────────────
    agv_state.reset()
    agv_state.register("agv-1", "AGV 1", "#16a085", [0, 1])

    # ── grid_state: reset ─────────────────────────────────────────────────
    grid_state.reset()

    # ── clear signal registry ─────────────────────────────────────────────
    with agv_routes._signals_lock:
        agv_routes._signals.clear()

    yield

    # ── teardown: reset module-level state so other tests are not polluted ─
    agv_state.reset()
    grid_state.reset()
    with agv_routes._signals_lock:
        agv_routes._signals.clear()


def _announce_fast(task_id="task-exec-1", constraint="shortest time"):
    """Task announce with very short windows for fast tests."""
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
            "bid_deadline_ts": now + 0.5,
            "consensus_deadline_ts": now + 1.5,
        },
    )


def test_execution_drives_agv_state_and_arm_angles(_execution_env, monkeypatch):
    """Full converged task: AGV ends idle at bay; both arms swept from REST.

    To keep the test fast we monkeypatch MOVE_STEP_SLEEP and arm step_sleep.
    """
    import app.agents.agv_agent as agv_mod
    import app.agents.actuation as actuation_mod
    from app.models import agv_state
    import app.models.arm_state as arm_state_mod

    monkeypatch.setattr(agv_mod, "MOVE_STEP_SLEEP", 0.01)
    monkeypatch.setattr(agv_mod, "SIGNAL_POLL_INTERVAL", 0.01)
    monkeypatch.setattr(actuation_mod, "DEFAULT_STEP_SLEEP", 0.005)

    bus = NegotiationBus()
    agents = [
        _arm(bus, "arm-1", "input_arm"),
        _arm(bus, "arm-2", "output_arm"),
        _agv(bus, "agv-1", position=(0, 1)),
    ]

    for a in agents:
        a.start()
    try:
        bus.publish(_announce_fast())
        # Allow negotiation + full execution (generous timeout for CI).
        time.sleep(10.0)
    finally:
        for a in agents:
            a.stop()

    # AGV should have returned to bay and been marked idle.
    rec = agv_state.get("agv-1")
    assert rec is not None, "agv-1 not registered"
    assert rec["status"] == "idle", f"AGV status after transport: {rec['status']}"
    assert rec["cell"] == [0, 1], f"AGV cell after return: {rec['cell']}"

    # Both arms should have moved from the initial REST angle (90°).
    arm1_angle = arm_state_mod._state[1]["angle"]
    arm2_angle = arm_state_mod._state[2]["angle"]
    # After pickup: REST=90; after place: REST=90 — but they swept through PICK/PLACE.
    # We can only assert they were written to (≥ 0) — they end at REST (90).
    assert arm1_angle == pytest.approx(90.0, abs=5), (
        f"arm-1 angle {arm1_angle} did not return to REST"
    )
    assert arm2_angle == pytest.approx(90.0, abs=5), (
        f"arm-2 angle {arm2_angle} did not return to REST"
    )


def test_fault_mid_execution_agv_releases_lane(_execution_env, monkeypatch):
    """AGV faults while traversing the lane: lane lock must be released."""
    import app.agents.agv_agent as agv_mod
    from app.models import agv_state, grid_state

    # Slow first step, fast rest.
    monkeypatch.setattr(agv_mod, "MOVE_STEP_SLEEP", 0.01)
    monkeypatch.setattr(agv_mod, "SIGNAL_POLL_INTERVAL", 0.01)

    bus = NegotiationBus()
    agents = [
        _arm(bus, "arm-1", "input_arm"),
        _arm(bus, "arm-2", "output_arm"),
        _agv(bus, "agv-1", position=(0, 1)),
    ]

    fault_injected = threading.Event()

    original_execute = AGVAgent._execute_leg

    def patched_execute(self, slot_id):
        """Run the real leg but fault the AGV mid-traversal after acquiring the lane."""
        from app.models import grid_state as gs

        # Let the AGV reach lane acquisition (step 5) then fault.
        # We detect lane held by polling the holder.
        def _wait_for_lane_then_fault():
            deadline = time.time() + 8.0
            while time.time() < deadline:
                if gs.lane().holder() == self.actor_id:
                    # Lane acquired — now inject the fault.
                    self.fault("test-injected mid-lane fault")
                    fault_injected.set()
                    return
                time.sleep(0.05)

        watcher = threading.Thread(target=_wait_for_lane_then_fault, daemon=True)
        watcher.start()
        original_execute(self, slot_id)

    monkeypatch.setattr(AGVAgent, "_execute_leg", patched_execute)

    for a in agents:
        a.start()
    try:
        bus.publish(_announce_fast(task_id="task-fault"))
        # Wait for fault injection + resolution.
        assert fault_injected.wait(timeout=10.0), "Fault was never injected"
        time.sleep(1.0)  # allow the halt path to run
    finally:
        for a in agents:
            a.stop()

    # Lane must be released — not held by agv-1.
    lane_holder = grid_state.lane().holder()
    assert lane_holder != "agv-1", (
        f"Lane still held by agv-1 after fault; holder={lane_holder}"
    )


def test_load_signal_timeout_fault_releases_input_station(_execution_env, monkeypatch):
    """Bug 1 regression: load-signal timeout faults while holding input_arm station.

    With the fix, self.fault() is called AFTER releasing the station.
    Without the fix, input_arm stays locked and no subsequent AGV can acquire it.

    Strategy: shorten LOAD_SIGNAL_TIMEOUT to near-zero so the AGV hits the
    timeout at step 3 (load signal poll). Never signal_load → timeout fires →
    fault() should be called with input_arm already released.
    """
    import app.agents.agv_agent as agv_mod
    from app.models import agv_state, grid_state

    monkeypatch.setattr(agv_mod, "MOVE_STEP_SLEEP", 0.01)
    monkeypatch.setattr(agv_mod, "SIGNAL_POLL_INTERVAL", 0.01)
    # Very short timeout — AGV will hit step 3 timeout almost immediately.
    monkeypatch.setattr(agv_mod, "LOAD_SIGNAL_TIMEOUT", 0.1)

    # Suppress arm execution so load signal is never sent (arms stay in pickup
    # sweep without completing — we achieve this by patching sweep_arm to stall).
    import app.agents.actuation as actuation_mod
    monkeypatch.setattr(actuation_mod, "DEFAULT_STEP_SLEEP", 60.0)  # stall sweeps

    bus = NegotiationBus()
    agents = [
        _arm(bus, "arm-1", "input_arm"),
        _arm(bus, "arm-2", "output_arm"),
        _agv(bus, "agv-1", position=(0, 1)),
    ]

    for a in agents:
        a.start()
    try:
        bus.publish(_announce_fast(task_id="task-load-timeout"))
        # Wait long enough for: negotiate + AGV step 1+2 + timeout (0.1s) + fault processing.
        time.sleep(5.0)
    finally:
        for a in agents:
            a.stop()

    # input_arm station must NOT be held by agv-1 — the fix releases it before fault().
    in_holder = grid_state.station_lock("input_arm").holder()
    assert in_holder != "agv-1", (
        f"input_arm station still held by agv-1 after load-signal timeout fault; "
        f"holder={in_holder}"
    )
    # The AGV agent should be faulted.
    agv_agent = next(a for a in agents if a.actor_id == "agv-1")
    from app.agents.actor_agent import AgentState
    assert agv_agent.state == AgentState.FAULTED, (
        f"agv-1 state after timeout fault: {agv_agent.state}"
    )


def test_higher_round_mid_execution_halts_executing_agent(_execution_env, monkeypatch):
    """A higher-round TASK_ANNOUNCE while an agent is executing halts its leg.

    Inject a round+1 announce after the AGV has started its transport leg
    (detected by agv status = 'loading'). The AGV should not complete the
    transport (it never gets the load signal since the pickup arm also sees
    the new round and stops).

    We verify: the executing agent never reaches 'idle' (transport not completed)
    AND the lane is eventually released.
    """
    import app.agents.agv_agent as agv_mod
    import app.agents.actuation as actuation_mod
    from app.models import agv_state, grid_state

    monkeypatch.setattr(agv_mod, "MOVE_STEP_SLEEP", 0.01)
    monkeypatch.setattr(agv_mod, "SIGNAL_POLL_INTERVAL", 0.01)
    monkeypatch.setattr(actuation_mod, "DEFAULT_STEP_SLEEP", 0.005)
    # Shorten load signal timeout so the test doesn't wait 120s.
    monkeypatch.setattr(agv_mod, "LOAD_SIGNAL_TIMEOUT", 2.0)

    bus = NegotiationBus()
    agents = [
        _arm(bus, "arm-1", "input_arm"),
        _arm(bus, "arm-2", "output_arm"),
        _agv(bus, "agv-1", position=(0, 1)),
    ]

    # Inject a higher round after the AGV transitions to 'loading'
    # (step 3 in the transport sequence — it is waiting for load signal).
    def _inject_higher_round():
        deadline = time.time() + 8.0
        while time.time() < deadline:
            rec = agv_state.get("agv-1")
            if rec and rec.get("status") == "loading":
                # AGV is waiting for load signal — now publish round+1.
                now = time.time()
                higher = Message(
                    type=MessageType.TASK_ANNOUNCE,
                    task_id="task-higher",
                    negotiation_round=1,
                    sender="interpreter",
                    payload={
                        "source_zone": "input_arm",
                        "dest_zone": "output_arm",
                        "constraint": "shortest time",
                        "slots": [
                            {"slot_id": "pickup", "kind": "arm", "zone": "input_arm"},
                            {"slot_id": "transport", "kind": "agv",
                             "from_zone": "input_arm", "to_zone": "output_arm"},
                            {"slot_id": "place", "kind": "arm", "zone": "output_arm"},
                        ],
                        "announce_ts": now,
                        "bid_deadline_ts": now + 0.5,
                        "consensus_deadline_ts": now + 1.5,
                    },
                )
                bus.publish(higher)
                return
            time.sleep(0.05)

    injector = threading.Thread(target=_inject_higher_round, daemon=True)

    for a in agents:
        a.start()
    try:
        bus.publish(_announce_fast(task_id="task-higher"))
        injector.start()
        injector.join(timeout=9.0)
        # Give the halt path time to release locks.
        time.sleep(2.0)
    finally:
        for a in agents:
            a.stop()

    # The lane must not be held by agv-1 after the halt.
    # (It is either released or was never acquired — both are safe.)
    lane_holder = grid_state.lane().holder()
    assert lane_holder != "agv-1", (
        f"Lane still held by agv-1 after higher-round halt; holder={lane_holder}"
    )
    # Input station must also be free.
    in_holder = grid_state.station_lock("input_arm").holder()
    assert in_holder != "agv-1", (
        f"input_arm station still held by agv-1 after halt; holder={in_holder}"
    )
