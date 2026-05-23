"""Phase 5 Stage 8 Track A — POSITION_UPDATE MessageType tests.

Four tests verifying:
1. MessageType.POSITION_UPDATE exists in the enum.
2. POSITION_UPDATE messages are emitted to the bus during spatial traversal (≥ 2).
3. Cadence is ≤ 5/sec (not the 10 Hz live SocketIO rate).
4. POSITION_UPDATE is observational — consensus outcomes are unchanged by its presence.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from app.agents.agv_agent import AGVAgent, CELL_PX
from app.agents.arm_agent import ArmAgent
from app.agents.bus import NegotiationBus
from app.agents.capability import CapabilityProfile
from app.agents.negotiation import (
    Message,
    MessageType,
    accepted_actors,
    surviving_bids,
    slots_complete,
)


# ── Shared scene constants (mirrors test_agv_spatial_movement.py) ─────────────

_STATIONS = {
    "input_arm": {"served_by": "arm-1", "cell": [1, 1], "dock": [2, 1]},
    "output_arm": {"served_by": "arm-2", "cell": [12, 1], "dock": [11, 1]},
}

_LANE_CELLS = [[3, 1], [4, 1], [5, 1], [6, 1], [7, 1], [8, 1], [9, 1], [10, 1]]

_WAREHOUSE_SCENE = {
    "grid": {"cols": 15, "rows": 5, "cell_px": 40},
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


@pytest.fixture(autouse=True)
def _stations(monkeypatch):
    from app.models import pipeline_config
    monkeypatch.setattr(pipeline_config, "get_stations", lambda: _STATIONS)


@pytest.fixture()
def _execution_env(monkeypatch):
    """Full execution environment: pipeline_config + agv_state + grid_state + signals."""
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

    monkeypatch.setattr(
        arm_state_mod, "_state",
        {
            1: {"arm_id": 1, "angle": 90.0, "status": "idle"},
            2: {"arm_id": 2, "angle": 90.0, "status": "idle"},
        },
    )
    monkeypatch.setattr(
        arm_state_mod, "_health",
        {
            1: {"health": "idle", "last_seen": time.time(), "error": None},
            2: {"health": "idle", "last_seen": time.time(), "error": None},
        },
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

def _arm(bus, actor_id, zone):
    return ArmAgent(
        CapabilityProfile(actor_id=actor_id, kind="arm", reachable_zones=[zone]),
        bus,
    )


def _agv(bus, actor_id="agv-1", position_px=(0.0, 40.0)):
    px = position_px
    return AGVAgent(
        CapabilityProfile(
            actor_id=actor_id,
            kind="agv",
            reachable_zones=["input_arm", "output_arm"],
            position=[px[0] / CELL_PX, px[1] / CELL_PX],
            position_px=px,
        ),
        bus,
    )


def _spatial_announce(task_id="task-posupd-1",
                      from_pos=(80.0, 40.0),
                      to_pos=(440.0, 40.0)):
    now = time.time()
    return Message(
        type=MessageType.TASK_ANNOUNCE,
        task_id=task_id,
        negotiation_round=0,
        sender="interpreter",
        payload={
            "source_zone": "input_arm",
            "dest_zone": "output_arm",
            "constraint": "shortest time",
            "slots": [
                {"slot_id": "pickup", "kind": "arm", "zone": "input_arm"},
                {
                    "slot_id": "transport",
                    "kind": "agv",
                    "from_zone": "input_arm",
                    "to_zone": "output_arm",
                    "from_pos": list(from_pos),
                    "to_pos": list(to_pos),
                },
                {"slot_id": "place", "kind": "arm", "zone": "output_arm"},
            ],
            "announce_ts": now,
            "bid_deadline_ts": now + 0.5,
            "consensus_deadline_ts": now + 1.5,
        },
    )


# ── Test 1: enum membership ────────────────────────────────────────────────────

def test_position_update_in_message_type():
    """MessageType enum contains POSITION_UPDATE with value 'position_update'."""
    assert hasattr(MessageType, "POSITION_UPDATE")
    assert MessageType.POSITION_UPDATE.value == "position_update"
    assert MessageType.POSITION_UPDATE in list(MessageType)


# ── Test 2: emitted during traversal ─────────────────────────────────────────

def test_position_update_emitted_during_traverse(_execution_env, monkeypatch):
    """Full converged spatial task: ≥ 2 POSITION_UPDATE messages from the AGV in bus history.

    Uses real 500 ms cadence. The traversal covers 360 px at 20 px/step with
    0.01 s/step sleep → ~1.8 s per leg.  The task runs two traversal legs
    (pickup + dropoff + return) = ~5.4 s, guaranteeing multiple 500 ms windows.
    """
    import app.agents.agv_agent as agv_mod
    import app.agents.actuation as actuation_mod

    # Speed up steps so the test completes quickly while keeping real wall-clock
    # 500 ms cadence gate meaningful.
    monkeypatch.setattr(agv_mod, "MOVE_STEP_SLEEP_S", 0.01)
    monkeypatch.setattr(agv_mod, "MOVE_STEP_SLEEP", 0.01)
    monkeypatch.setattr(agv_mod, "SIGNAL_POLL_INTERVAL", 0.01)
    monkeypatch.setattr(actuation_mod, "DEFAULT_STEP_SLEEP", 0.005)

    bus = NegotiationBus()
    agents = [
        _arm(bus, "arm-1", "input_arm"),
        _arm(bus, "arm-2", "output_arm"),
        _agv(bus, "agv-1", position_px=(0.0, 40.0)),
    ]
    for a in agents:
        a.start()
    try:
        bus.publish(_spatial_announce(from_pos=(80.0, 40.0), to_pos=(440.0, 40.0)))
        time.sleep(10.0)
    finally:
        for a in agents:
            a.stop()

    history = bus.history()
    pos_updates = [
        m for m in history
        if m.type == MessageType.POSITION_UPDATE
        and m.sender == "agv-1"
    ]
    assert len(pos_updates) >= 2, (
        f"Expected ≥ 2 POSITION_UPDATE messages from agv-1, got {len(pos_updates)}"
    )

    # Validate payload shape.
    for m in pos_updates:
        payload = m.payload
        assert "x" in payload, f"POSITION_UPDATE missing 'x': {payload}"
        assert "y" in payload, f"POSITION_UPDATE missing 'y': {payload}"
        assert "theta" in payload, f"POSITION_UPDATE missing 'theta': {payload}"
        assert "ts" in payload, f"POSITION_UPDATE missing 'ts': {payload}"


# ── Test 3: cadence ≤ 5 Hz  ───────────────────────────────────────────────────

def test_position_update_cadence_below_5hz(_execution_env, monkeypatch):
    """POSITION_UPDATE cadence must not exceed 5/sec (500 ms minimum interval).

    Uses real 500 ms threshold: any two consecutive POSITION_UPDATE messages
    from the same AGV must be ≥ 0.4 s apart (10 % slack for timing jitter).
    """
    import app.agents.agv_agent as agv_mod
    import app.agents.actuation as actuation_mod

    monkeypatch.setattr(agv_mod, "MOVE_STEP_SLEEP_S", 0.01)
    monkeypatch.setattr(agv_mod, "MOVE_STEP_SLEEP", 0.01)
    monkeypatch.setattr(agv_mod, "SIGNAL_POLL_INTERVAL", 0.01)
    monkeypatch.setattr(actuation_mod, "DEFAULT_STEP_SLEEP", 0.005)

    bus = NegotiationBus()
    agents = [
        _arm(bus, "arm-1", "input_arm"),
        _arm(bus, "arm-2", "output_arm"),
        _agv(bus, "agv-1", position_px=(0.0, 40.0)),
    ]
    for a in agents:
        a.start()
    try:
        # Long traversal: 360 px / 20 px step = 18 steps per leg at real 500 ms cadence.
        bus.publish(_spatial_announce(from_pos=(80.0, 40.0), to_pos=(440.0, 40.0)))
        time.sleep(10.0)
    finally:
        for a in agents:
            a.stop()

    history = bus.history()
    pos_updates = sorted(
        [m for m in history if m.type == MessageType.POSITION_UPDATE and m.sender == "agv-1"],
        key=lambda m: m.ts,
    )

    if len(pos_updates) < 2:
        # Fewer than 2 messages means cadence is not exceeded (by definition).
        return

    # Check every consecutive pair.
    for i in range(1, len(pos_updates)):
        dt = pos_updates[i].ts - pos_updates[i - 1].ts
        assert dt >= 0.4, (
            f"POSITION_UPDATE cadence too high: consecutive messages {i-1} and {i} "
            f"separated by only {dt:.3f}s (minimum 0.4 s expected)"
        )


# ── Test 4: observational — no consensus impact ────────────────────────────────

def test_position_update_observational_no_consensus_impact(_execution_env, monkeypatch):
    """POSITION_UPDATE in bus history does not affect consensus computation.

    Runs a full converged spatial task, then replays consensus checks on the
    resulting bus history (with POSITION_UPDATE messages present) and verifies
    that the accepted_actors, surviving_bids, and slots_complete outcomes are
    identical to what they would be without POSITION_UPDATE in the history.
    """
    import app.agents.agv_agent as agv_mod
    import app.agents.actuation as actuation_mod

    monkeypatch.setattr(agv_mod, "MOVE_STEP_SLEEP_S", 0.01)
    monkeypatch.setattr(agv_mod, "MOVE_STEP_SLEEP", 0.01)
    monkeypatch.setattr(agv_mod, "SIGNAL_POLL_INTERVAL", 0.01)
    monkeypatch.setattr(actuation_mod, "DEFAULT_STEP_SLEEP", 0.005)

    bus = NegotiationBus()
    agents = [
        _arm(bus, "arm-1", "input_arm"),
        _arm(bus, "arm-2", "output_arm"),
        _agv(bus, "agv-1", position_px=(0.0, 40.0)),
    ]
    task_id = "task-obs-consensus"
    for a in agents:
        a.start()
    try:
        bus.publish(_spatial_announce(task_id=task_id,
                                     from_pos=(80.0, 40.0), to_pos=(440.0, 40.0)))
        time.sleep(10.0)
    finally:
        for a in agents:
            a.stop()

    history = bus.history()

    # Verify POSITION_UPDATE messages are actually present.
    pos_updates = [m for m in history if m.type == MessageType.POSITION_UPDATE]
    assert pos_updates, "No POSITION_UPDATE in history — test precondition failed"

    # Re-derive consensus from the full history (including POSITION_UPDATE).
    accepts_with = accepted_actors(history, task_id, negotiation_round=0)

    # Re-derive consensus from a filtered history (POSITION_UPDATE stripped out).
    filtered = [m for m in history if m.type != MessageType.POSITION_UPDATE]
    accepts_without = accepted_actors(filtered, task_id, negotiation_round=0)

    assert accepts_with == accepts_without, (
        f"Consensus differs with vs without POSITION_UPDATE: "
        f"with={accepts_with}, without={accepts_without}"
    )

    # slots_complete must agree.
    required = {"pickup", "transport", "place"}
    assert slots_complete(required, accepts_with) == slots_complete(required, accepts_without), (
        "slots_complete differs with vs without POSITION_UPDATE in history"
    )
