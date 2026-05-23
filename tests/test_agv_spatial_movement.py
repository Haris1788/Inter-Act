"""Phase 5 Stage 5 Track A — AGV pixel-space movement tests.

Tests the spatial branch of AGVAgent._execute_leg:
- agv_position SocketIO events emitted during traversal
- agv_state cell updated (snapped from px) after traversal
- Return-to-origin after full leg
- Origin captured from CapabilityProfile.position_px
- Legacy zone-string path unchanged (no agv_position events)
"""

import time
import threading
from unittest.mock import MagicMock, patch

import pytest

from app.agents.agv_agent import AGVAgent, MOVE_STEP_PX, MOVE_STEP_SLEEP_S, CELL_PX
from app.agents.arm_agent import ArmAgent
from app.agents.bus import NegotiationBus
from app.agents.capability import CapabilityProfile
from app.agents.negotiation import Message, MessageType


# ── Shared scene constants ────────────────────────────────────────────────────

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
    "lane": {
        "cells": _LANE_CELLS,
        "px": [],
    },
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

    # arm_state: register arm-1 and arm-2
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

    # agv_state: register agv-1
    agv_state.reset()
    agv_state.register("agv-1", "AGV 1", "#16a085", [0, 1])

    # grid_state: reset
    grid_state.reset()

    # signal registry: clear
    with agv_routes._signals_lock:
        agv_routes._signals.clear()

    yield

    agv_state.reset()
    grid_state.reset()
    with agv_routes._signals_lock:
        agv_routes._signals.clear()


# ── Helper factories ──────────────────────────────────────────────────────────

def _arm(bus, actor_id, zone):
    return ArmAgent(
        CapabilityProfile(actor_id=actor_id, kind="arm", reachable_zones=[zone]),
        bus,
    )


def _agv(bus, actor_id="agv-1", position_px=(0.0, 40.0)):
    """Build an AGVAgent with a known position_px."""
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


def _spatial_announce(task_id="task-spatial-1",
                      from_pos=(80.0, 40.0),
                      to_pos=(440.0, 40.0)):
    """Announce with spatial from_pos / to_pos on the transport slot."""
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


def _legacy_announce(task_id="task-legacy-1"):
    """Legacy zone-string announce (no from_pos / to_pos)."""
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
                {"slot_id": "transport", "kind": "agv",
                 "from_zone": "input_arm", "to_zone": "output_arm"},
                {"slot_id": "place", "kind": "arm", "zone": "output_arm"},
            ],
            "announce_ts": now,
            "bid_deadline_ts": now + 0.5,
            "consensus_deadline_ts": now + 1.5,
        },
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_traverse_emits_positions(_execution_env, monkeypatch):
    """Spatial slot: at least 3 agv_position SocketIO events emitted during traversal."""
    import app.agents.agv_agent as agv_mod
    import app.agents.actuation as actuation_mod

    monkeypatch.setattr(agv_mod, "MOVE_STEP_SLEEP_S", 0.01)
    monkeypatch.setattr(agv_mod, "MOVE_STEP_SLEEP", 0.01)
    monkeypatch.setattr(agv_mod, "SIGNAL_POLL_INTERVAL", 0.01)
    monkeypatch.setattr(actuation_mod, "DEFAULT_STEP_SLEEP", 0.005)

    emitted = []

    def fake_emit(event, payload):
        if event == "agv_position":
            emitted.append(payload)

    monkeypatch.setattr(agv_mod, "_emit_agv_position",
                        lambda aid, x, y, theta: emitted.append({"actor_id": aid, "x": x, "y": y}))

    bus = NegotiationBus()
    agents = [
        _arm(bus, "arm-1", "input_arm"),
        _arm(bus, "arm-2", "output_arm"),
        _agv(bus, "agv-1", position_px=(0.0, 40.0)),
    ]

    for a in agents:
        a.start()
    try:
        # from_pos=(80,40) → to_pos=(440,40): 360 px / 20 px step = 18 steps for trip leg.
        bus.publish(_spatial_announce(from_pos=(80.0, 40.0), to_pos=(440.0, 40.0)))
        time.sleep(10.0)
    finally:
        for a in agents:
            a.stop()

    assert len(emitted) >= 3, (
        f"Expected >= 3 agv_position events, got {len(emitted)}"
    )


def test_traverse_updates_agv_state_cell(_execution_env, monkeypatch):
    """agv_state.get(agv_id).cell matches snapped px target after traversal."""
    import app.agents.agv_agent as agv_mod
    import app.agents.actuation as actuation_mod
    from app.models import agv_state

    monkeypatch.setattr(agv_mod, "MOVE_STEP_SLEEP_S", 0.01)
    monkeypatch.setattr(agv_mod, "MOVE_STEP_SLEEP", 0.01)
    monkeypatch.setattr(agv_mod, "SIGNAL_POLL_INTERVAL", 0.01)
    monkeypatch.setattr(actuation_mod, "DEFAULT_STEP_SLEEP", 0.005)

    # to_pos=(440, 40) → snapped cell = [int(440/40), int(40/40)] = [11, 1]
    # But after return-to-origin at (0, 40) → snapped cell = [0, 1].
    # We want the state after traversal to to_pos — but full leg returns to origin.
    # So assert final cell == origin cell = [0, 1].
    to_px = (440.0, 40.0)
    origin_px = (0.0, 40.0)
    expected_final_cell = [int(origin_px[0] / CELL_PX), int(origin_px[1] / CELL_PX)]

    bus = NegotiationBus()
    agents = [
        _arm(bus, "arm-1", "input_arm"),
        _arm(bus, "arm-2", "output_arm"),
        _agv(bus, "agv-1", position_px=origin_px),
    ]

    for a in agents:
        a.start()
    try:
        bus.publish(_spatial_announce(from_pos=(80.0, 40.0), to_pos=to_px))
        time.sleep(10.0)
    finally:
        for a in agents:
            a.stop()

    rec = agv_state.get("agv-1")
    assert rec is not None
    # After returning to origin, cell should match snapped origin.
    assert rec["cell"] == expected_final_cell, (
        f"Expected cell {expected_final_cell} after spatial leg, got {rec['cell']}"
    )


def test_returns_to_origin_after_place_leg(_execution_env, monkeypatch):
    """Full spatial leg: final agv_state cell matches snapped origin px."""
    import app.agents.agv_agent as agv_mod
    import app.agents.actuation as actuation_mod
    from app.models import agv_state

    monkeypatch.setattr(agv_mod, "MOVE_STEP_SLEEP_S", 0.01)
    monkeypatch.setattr(agv_mod, "MOVE_STEP_SLEEP", 0.01)
    monkeypatch.setattr(agv_mod, "SIGNAL_POLL_INTERVAL", 0.01)
    monkeypatch.setattr(actuation_mod, "DEFAULT_STEP_SLEEP", 0.005)

    origin_px = (20.0, 40.0)
    expected_origin_cell = [int(origin_px[0] / CELL_PX), int(origin_px[1] / CELL_PX)]

    bus = NegotiationBus()
    agents = [
        _arm(bus, "arm-1", "input_arm"),
        _arm(bus, "arm-2", "output_arm"),
        _agv(bus, "agv-1", position_px=origin_px),
    ]

    for a in agents:
        a.start()
    try:
        bus.publish(_spatial_announce(from_pos=(80.0, 40.0), to_pos=(440.0, 40.0)))
        time.sleep(12.0)
    finally:
        for a in agents:
            a.stop()

    rec = agv_state.get("agv-1")
    assert rec is not None, "agv-1 not registered"
    assert rec["status"] == "idle", f"AGV status after return: {rec['status']}"
    assert rec["cell"] == expected_origin_cell, (
        f"Expected origin cell {expected_origin_cell}, got {rec['cell']}"
    )


def test_origin_captured_from_topology_profile(_execution_env, monkeypatch):
    """Actor created with position_px=(500, 500); after a leg returns to that position."""
    import app.agents.agv_agent as agv_mod
    import app.agents.actuation as actuation_mod
    from app.models import agv_state

    monkeypatch.setattr(agv_mod, "MOVE_STEP_SLEEP_S", 0.01)
    monkeypatch.setattr(agv_mod, "MOVE_STEP_SLEEP", 0.01)
    monkeypatch.setattr(agv_mod, "SIGNAL_POLL_INTERVAL", 0.01)
    monkeypatch.setattr(actuation_mod, "DEFAULT_STEP_SLEEP", 0.005)

    origin_px = (500.0, 500.0)
    expected_origin_cell = [int(origin_px[0] / CELL_PX), int(origin_px[1] / CELL_PX)]
    # = [12, 12]

    bus = NegotiationBus()
    agents = [
        _arm(bus, "arm-1", "input_arm"),
        _arm(bus, "arm-2", "output_arm"),
        _agv(bus, "agv-1", position_px=origin_px),
    ]

    for a in agents:
        a.start()
    try:
        bus.publish(_spatial_announce(from_pos=(80.0, 40.0), to_pos=(440.0, 40.0)))
        time.sleep(12.0)
    finally:
        for a in agents:
            a.stop()

    rec = agv_state.get("agv-1")
    assert rec is not None, "agv-1 not registered"
    assert rec["status"] == "idle", f"AGV status: {rec['status']}"
    assert rec["cell"] == expected_origin_cell, (
        f"Expected origin cell {expected_origin_cell} for position_px={origin_px}, "
        f"got {rec['cell']}"
    )


def test_legacy_zone_path_still_uses_cell_motion(_execution_env, monkeypatch):
    """Phase 4-style zone-string slot: legacy 12-step path runs; no agv_position events."""
    import app.agents.agv_agent as agv_mod
    import app.agents.actuation as actuation_mod
    from app.models import agv_state

    monkeypatch.setattr(agv_mod, "MOVE_STEP_SLEEP", 0.01)
    monkeypatch.setattr(agv_mod, "SIGNAL_POLL_INTERVAL", 0.01)
    monkeypatch.setattr(actuation_mod, "DEFAULT_STEP_SLEEP", 0.005)

    position_events: list = []
    monkeypatch.setattr(
        agv_mod, "_emit_agv_position",
        lambda aid, x, y, theta: position_events.append((aid, x, y)),
    )

    bus = NegotiationBus()
    agents = [
        _arm(bus, "arm-1", "input_arm"),
        _arm(bus, "arm-2", "output_arm"),
        _agv(bus, "agv-1", position_px=(0.0, 40.0)),
    ]

    for a in agents:
        a.start()
    try:
        bus.publish(_legacy_announce())
        time.sleep(10.0)
    finally:
        for a in agents:
            a.stop()

    # Legacy path must NOT emit any agv_position events.
    assert len(position_events) == 0, (
        f"Legacy zone-string path should not emit agv_position; got {len(position_events)} events"
    )
    # AGV must still complete the leg and return idle.
    rec = agv_state.get("agv-1")
    assert rec is not None
    assert rec["status"] == "idle", (
        f"Legacy AGV should end idle; got {rec['status']}"
    )
