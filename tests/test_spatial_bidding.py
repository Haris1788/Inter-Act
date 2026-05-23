"""Spatial bidding unit tests — Phase 5 Stage 4 acceptance gate.

THE load-bearing test: `test_acceptance_gate_position_dictates_winner`.
If this test does not pass, Phase 5 is failed by definition.
"""

import math
import time

import pytest

from app.agents.agv_agent import AGVAgent
from app.agents.arm_agent import ArmAgent
from app.agents.bus import NegotiationBus
from app.agents.capability import CapabilityProfile
from app.agents.negotiation import (
    Message,
    MessageType,
    Slot,
    accepted_actors,
    derive_slots_by_actor,
    public_winner,
    surviving_bids,
)
from app.models.topology import ActorSpec, Topology


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_topology(arm_specs, agv_specs, reach_px=300.0):
    """Build a Topology from lists of (id, x, y) tuples."""
    actors = tuple(
        ActorSpec(id=a[0], kind="arm", x=a[1], y=a[2], reach_px=reach_px)
        for a in arm_specs
    ) + tuple(
        ActorSpec(id=a[0], kind="agv", x=a[1], y=a[2])
        for a in agv_specs
    )
    return Topology(actors=actors)


def _arm_agent(bus, actor_id, position_px, reach_px=500.0, reachable_zones=None):
    profile = CapabilityProfile(
        actor_id=actor_id,
        kind="arm",
        reachable_zones=reachable_zones or [actor_id],
        position_px=position_px,
        reach_px=reach_px,
    )
    return ArmAgent(profile, bus)


def _agv_agent(bus, actor_id, position_px, capacity=1):
    profile = CapabilityProfile(
        actor_id=actor_id,
        kind="agv",
        reachable_zones=[],
        position=[0.0, 0.0],
        position_px=position_px,
        capacity=capacity,
    )
    return AGVAgent(profile, bus)


def _announce(task_id, slots, constraint=""):
    now = time.time()
    return Message(
        type=MessageType.TASK_ANNOUNCE,
        task_id=task_id,
        negotiation_round=0,
        sender="interpreter",
        payload={
            "constraint": constraint,
            "slots": [_slot_dict(s) for s in slots],
            "announce_ts": now,
            "bid_deadline_ts": now + 1.5,
            "consensus_deadline_ts": now + 3.0,
        },
    )


def _slot_dict(slot: Slot) -> dict:
    d: dict = {"slot_id": slot.slot_id, "kind": slot.kind}
    if slot.zone is not None:
        d["zone"] = slot.zone
    if slot.from_zone is not None:
        d["from_zone"] = slot.from_zone
    if slot.to_zone is not None:
        d["to_zone"] = slot.to_zone
    if slot.target_pos is not None:
        d["target_pos"] = list(slot.target_pos)
    if slot.from_pos is not None:
        d["from_pos"] = list(slot.from_pos)
    if slot.to_pos is not None:
        d["to_pos"] = list(slot.to_pos)
    if slot.served_by is not None:
        d["served_by"] = slot.served_by
    return d


def _run_negotiation(agents, announce, settle=4.0):
    """Start agents, publish announce, wait for consensus, stop and return history."""
    for a in agents:
        a.start()
    try:
        bus = agents[0].bus
        bus.publish(announce)
        time.sleep(settle)
        return bus.history()
    finally:
        for a in agents:
            a.stop()


# ---------------------------------------------------------------------------
# Test 1 — arm bid score changes with placement
# ---------------------------------------------------------------------------

def test_placement_changes_arm_bid_score():
    """Closer arm has strictly lower bid score (shorter Euclidean distance)."""
    # Pickup slot with target_pos at (500, 500).
    slot = {"slot_id": "pickup", "kind": "arm", "target_pos": [500.0, 500.0]}

    # arm-near is 100 px away; arm-far is 700 px away.
    profile_near = CapabilityProfile(
        actor_id="arm-near", kind="arm",
        position_px=(400.0, 500.0), reach_px=500.0,
    )
    profile_far = CapabilityProfile(
        actor_id="arm-far", kind="arm",
        position_px=(1200.0, 500.0), reach_px=1000.0,
    )
    bus = NegotiationBus()
    near = ArmAgent(profile_near, bus)
    far = ArmAgent(profile_far, bus)

    score_near = near._bid_score(slot)
    score_far = far._bid_score(slot)

    assert score_near is not None, "arm-near should bid (in reach)"
    assert score_far is not None, "arm-far should bid (in reach)"
    assert score_near < score_far, (
        f"Closer arm should have lower score: near={score_near:.2f} far={score_far:.2f}"
    )
    # Exact distances: near = 100, far = 700.
    assert math.isclose(score_near, 100.0, abs_tol=0.01)
    assert math.isclose(score_far, 700.0, abs_tol=0.01)


# ---------------------------------------------------------------------------
# Test 2 — arm out of reach returns None
# ---------------------------------------------------------------------------

def test_arm_out_of_reach_returns_none():
    """Arm with reach_px=100; target at distance 200 → None (no bid)."""
    slot = {"slot_id": "pickup", "kind": "arm", "target_pos": [300.0, 200.0]}
    profile = CapabilityProfile(
        actor_id="arm-1", kind="arm",
        position_px=(100.0, 200.0), reach_px=100.0,  # 200 px away, reach=100
    )
    bus = NegotiationBus()
    agent = ArmAgent(profile, bus)
    assert agent._bid_score(slot) is None


# ---------------------------------------------------------------------------
# Test 3 — AGV bid score changes with placement
# ---------------------------------------------------------------------------

def test_placement_changes_agv_bid_score():
    """AGV closer to pickup has lower bid score; farther AGV has higher score."""
    # Transport slot: pickup at (400, 200), dropoff at (1200, 200).
    slot = {
        "slot_id": "transport",
        "kind": "agv",
        "from_pos": [400.0, 200.0],
        "to_pos": [1200.0, 200.0],
    }
    bus = NegotiationBus()
    # agv-near: at (300, 200) — approach = 100, trip = 800 → total = 900.
    agv_near = _agv_agent(bus, "agv-near", position_px=(300.0, 200.0))
    # agv-far: at (1300, 200) — approach = 900, trip = 800 → total = 1700.
    agv_far = _agv_agent(bus, "agv-far", position_px=(1300.0, 200.0))

    score_near = agv_near._bid_score(slot)
    score_far = agv_far._bid_score(slot)

    assert score_near is not None
    assert score_far is not None
    assert score_near < score_far, (
        f"Closer AGV should have lower score: near={score_near:.2f} far={score_far:.2f}"
    )


# ---------------------------------------------------------------------------
# Test 4 — THE ACCEPTANCE GATE
# ---------------------------------------------------------------------------

def test_acceptance_gate_position_dictates_winner():
    """Moving an AGV on the canvas MUST change bid scores and winners.

    Topology:
        arm-1 at (200, 200), arm-2 at (1400, 200)  — source + dest
        agv-1 at (300, 200), agv-2 at (1300, 200)

    Under "shortest time": agv-1 is closer to arm-1 (source) → agv-1 wins.
    Under "longest time":  agv-2 is farther from arm-1 → agv-2 wins.
    Move agv-1 to (1500, 1500) → agv-2 is now closer to source → agv-2 wins
    even under "shortest time".
    """
    topology = _make_topology(
        arm_specs=[("arm-1", 200.0, 200.0), ("arm-2", 1400.0, 200.0)],
        agv_specs=[("agv-1", 300.0, 200.0), ("agv-2", 1300.0, 200.0)],
        reach_px=400.0,
    )
    slots = derive_slots_by_actor("arm-1", "arm-2", topology)
    transport_slot = next(s for s in slots if s.kind == "agv")

    # ── Scenario A: shortest time ──────────────────────────────────────────
    bus_a = NegotiationBus()
    arm1_a = _arm_agent(bus_a, "arm-1", position_px=(200.0, 200.0), reach_px=400.0)
    arm2_a = _arm_agent(bus_a, "arm-2", position_px=(1400.0, 200.0), reach_px=400.0)
    agv1_a = _agv_agent(bus_a, "agv-1", position_px=(300.0, 200.0))
    agv2_a = _agv_agent(bus_a, "agv-2", position_px=(1300.0, 200.0))

    history_a = _run_negotiation(
        [arm1_a, arm2_a, agv1_a, agv2_a],
        _announce("task-A", slots, constraint="shortest time"),
        settle=4.0,
    )
    accepts_a = accepted_actors(history_a, "task-A", 0)
    assert "transport" in accepts_a, "Consensus must include transport slot"
    assert accepts_a["transport"] == "agv-1", (
        f"Under 'shortest time' agv-1 (closer to source) should win; got {accepts_a['transport']}"
    )

    # ── Scenario B: longest time ───────────────────────────────────────────
    bus_b = NegotiationBus()
    arm1_b = _arm_agent(bus_b, "arm-1", position_px=(200.0, 200.0), reach_px=400.0)
    arm2_b = _arm_agent(bus_b, "arm-2", position_px=(1400.0, 200.0), reach_px=400.0)
    agv1_b = _agv_agent(bus_b, "agv-1", position_px=(300.0, 200.0))
    agv2_b = _agv_agent(bus_b, "agv-2", position_px=(1300.0, 200.0))

    history_b = _run_negotiation(
        [arm1_b, arm2_b, agv1_b, agv2_b],
        _announce("task-B", slots, constraint="longest time"),
        settle=4.0,
    )
    accepts_b = accepted_actors(history_b, "task-B", 0)
    assert "transport" in accepts_b, "Consensus must include transport slot"
    assert accepts_b["transport"] == "agv-2", (
        f"Under 'longest time' agv-2 (farther from source) should win; got {accepts_b['transport']}"
    )

    # ── Scenario C: move agv-1 far away; shortest time should now pick agv-2 ─
    # New topology: agv-1 at (1500, 1500) — much farther from arm-1 (200, 200).
    topology_c = _make_topology(
        arm_specs=[("arm-1", 200.0, 200.0), ("arm-2", 1400.0, 200.0)],
        agv_specs=[("agv-1", 1500.0, 1500.0), ("agv-2", 1300.0, 200.0)],
        reach_px=400.0,
    )
    slots_c = derive_slots_by_actor("arm-1", "arm-2", topology_c)

    bus_c = NegotiationBus()
    arm1_c = _arm_agent(bus_c, "arm-1", position_px=(200.0, 200.0), reach_px=400.0)
    arm2_c = _arm_agent(bus_c, "arm-2", position_px=(1400.0, 200.0), reach_px=400.0)
    # agv-1 moved to (1500, 1500)
    agv1_c = _agv_agent(bus_c, "agv-1", position_px=(1500.0, 1500.0))
    agv2_c = _agv_agent(bus_c, "agv-2", position_px=(1300.0, 200.0))

    history_c = _run_negotiation(
        [arm1_c, arm2_c, agv1_c, agv2_c],
        _announce("task-C", slots_c, constraint="shortest time"),
        settle=4.0,
    )
    accepts_c = accepted_actors(history_c, "task-C", 0)
    assert "transport" in accepts_c, "Consensus must include transport slot"
    assert accepts_c["transport"] == "agv-2", (
        f"After moving agv-1 far, agv-2 should win 'shortest time'; got {accepts_c['transport']}"
    )


# ---------------------------------------------------------------------------
# Test 5 — heaviest load constraint
# ---------------------------------------------------------------------------

def test_heaviest_load_constraint_picks_max_capacity():
    """Under 'heaviest load', AGV with capacity=4 wins over capacity=1."""
    slot = {
        "slot_id": "transport",
        "kind": "agv",
        "from_pos": [400.0, 200.0],
        "to_pos": [1200.0, 200.0],
    }
    bus = NegotiationBus()
    agv_light = _agv_agent(bus, "agv-light", position_px=(300.0, 200.0), capacity=1)
    agv_heavy = _agv_agent(bus, "agv-heavy", position_px=(1300.0, 200.0), capacity=4)

    score_light = agv_light._bid_score(slot)
    score_heavy = agv_heavy._bid_score(slot)

    assert score_light is not None
    assert score_heavy is not None

    # Build fake bid messages as public_winner would see them.
    from app.agents.negotiation import Message, MessageType
    bids = [
        Message(
            type=MessageType.BID, task_id="t1", negotiation_round=0,
            sender="agv-light", slot_id="transport",
            payload={"score": score_light, "capacity": 1},
        ),
        Message(
            type=MessageType.BID, task_id="t1", negotiation_round=0,
            sender="agv-heavy", slot_id="transport",
            payload={"score": score_heavy, "capacity": 4},
        ),
    ]
    winner = public_winner(bids, "heaviest load")
    assert winner == "agv-heavy", (
        f"'heaviest load' should pick agv-heavy (cap=4); got {winner}"
    )


# ---------------------------------------------------------------------------
# Test 6 — legacy zone path unchanged
# ---------------------------------------------------------------------------

def test_legacy_zone_path_unchanged():
    """Slots with only zone/from_zone/to_zone (no target_pos/from_pos) use Phase 4 logic."""
    # ARM: zone-only slot — falls back to zone-match (score = 0.0).
    arm_slot = {"slot_id": "pickup", "kind": "arm", "zone": "input_arm"}
    arm_profile = CapabilityProfile(
        actor_id="arm-1", kind="arm",
        reachable_zones=["input_arm"],
        position_px=(100.0, 100.0),
        reach_px=200.0,
    )
    bus = NegotiationBus()
    arm = ArmAgent(arm_profile, bus)
    score = arm._bid_score(arm_slot)
    assert score == 0.0, "Legacy zone-match path should return 0.0"

    # ARM: zone not in reachable_zones → None.
    assert arm._bid_score({"slot_id": "pickup", "kind": "arm", "zone": "output_arm"}) is None

    # AGV: from_zone only (no from_pos) — falls back to legacy Manhattan.
    # Without pipeline_config, _dock_cell returns None → score is None.
    agv_slot = {
        "slot_id": "transport",
        "kind": "agv",
        "from_zone": "input_arm",
        "to_zone": "output_arm",
    }
    agv_profile = CapabilityProfile(
        actor_id="agv-1", kind="agv",
        reachable_zones=["input_arm", "output_arm"],
        position=[3.0, 1.0],
        position_px=(120.0, 40.0),
    )
    agv = AGVAgent(agv_profile, bus)
    # When from_pos/to_pos are absent the legacy path is taken.
    # Since pipeline_config.get_stations() isn't mocked here, _dock_cell → None → None.
    score_agv = agv._bid_score(agv_slot)
    # The legacy path either returns a number (if stations configured) or None.
    # What matters: no exception, and it did NOT try to use from_pos/to_pos.
    assert score_agv is None or isinstance(score_agv, (int, float))
