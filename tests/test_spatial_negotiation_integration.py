"""Spatial negotiation integration test — Phase 5 Stage 4.

Full multi-agent loop (ArmAgent + AGVAgent) against topology-based announce.
Confirms end-to-end spatial selection: actors at different positions produce
different winners when the constraint changes.
"""

import time

import pytest

from app.agents.agv_agent import AGVAgent
from app.agents.arm_agent import ArmAgent
from app.agents.bus import NegotiationBus
from app.agents.capability import CapabilityProfile
from app.agents.negotiation import (
    Message,
    MessageType,
    accepted_actors,
    derive_slots_by_actor,
)
from app.models.topology import ActorSpec, Topology


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _topology():
    """Fixed topology for all integration tests in this file."""
    return Topology(
        actors=(
            ActorSpec(id="arm-1", kind="arm", x=200.0, y=200.0, reach_px=400.0),
            ActorSpec(id="arm-2", kind="arm", x=1400.0, y=200.0, reach_px=400.0),
            ActorSpec(id="agv-1", kind="agv", x=300.0, y=200.0),
            ActorSpec(id="agv-2", kind="agv", x=1300.0, y=200.0),
        )
    )


def _arm(bus, actor_id, pos_px, reach_px=400.0):
    return ArmAgent(
        CapabilityProfile(
            actor_id=actor_id, kind="arm",
            reachable_zones=[actor_id],
            position_px=pos_px, reach_px=reach_px,
        ),
        bus,
    )


def _agv(bus, actor_id, pos_px):
    return AGVAgent(
        CapabilityProfile(
            actor_id=actor_id, kind="agv",
            reachable_zones=[],
            position=[0.0, 0.0],
            position_px=pos_px,
        ),
        bus,
    )


def _announce(task_id, slots, constraint=""):
    now = time.time()
    slot_dicts = []
    for s in slots:
        d: dict = {"slot_id": s.slot_id, "kind": s.kind}
        if s.zone is not None:
            d["zone"] = s.zone
        if s.from_zone is not None:
            d["from_zone"] = s.from_zone
        if s.to_zone is not None:
            d["to_zone"] = s.to_zone
        if s.target_pos is not None:
            d["target_pos"] = list(s.target_pos)
        if s.from_pos is not None:
            d["from_pos"] = list(s.from_pos)
        if s.to_pos is not None:
            d["to_pos"] = list(s.to_pos)
        if s.served_by is not None:
            d["served_by"] = s.served_by
        slot_dicts.append(d)
    return Message(
        type=MessageType.TASK_ANNOUNCE,
        task_id=task_id,
        negotiation_round=0,
        sender="interpreter",
        payload={
            "constraint": constraint,
            "slots": slot_dicts,
            "announce_ts": now,
            "bid_deadline_ts": now + 1.5,
            "consensus_deadline_ts": now + 3.0,
        },
    )


def _run(agents, announce, settle=4.0):
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
# Integration tests
# ---------------------------------------------------------------------------

def test_spatial_shortest_time_picks_closer_agv():
    """Under 'shortest time', the AGV closer to the source arm wins."""
    topo = _topology()
    slots = derive_slots_by_actor("arm-1", "arm-2", topo)

    bus = NegotiationBus()
    agents = [
        _arm(bus, "arm-1", pos_px=(200.0, 200.0)),
        _arm(bus, "arm-2", pos_px=(1400.0, 200.0)),
        _agv(bus, "agv-1", pos_px=(300.0, 200.0)),   # closer to arm-1
        _agv(bus, "agv-2", pos_px=(1300.0, 200.0)),  # farther
    ]
    history = _run(agents, _announce("task-st", slots, constraint="shortest time"))
    accepts = accepted_actors(history, "task-st", 0)

    assert "transport" in accepts, f"No transport slot accepted; accepts={accepts}"
    assert accepts["transport"] == "agv-1", (
        f"Under 'shortest time', agv-1 (closer) should win; got {accepts['transport']}"
    )


def test_spatial_longest_time_picks_farther_agv():
    """Under 'longest time', the AGV farther from the source arm wins."""
    topo = _topology()
    slots = derive_slots_by_actor("arm-1", "arm-2", topo)

    bus = NegotiationBus()
    agents = [
        _arm(bus, "arm-1", pos_px=(200.0, 200.0)),
        _arm(bus, "arm-2", pos_px=(1400.0, 200.0)),
        _agv(bus, "agv-1", pos_px=(300.0, 200.0)),   # closer
        _agv(bus, "agv-2", pos_px=(1300.0, 200.0)),  # farther → wins
    ]
    history = _run(agents, _announce("task-lt", slots, constraint="longest time"))
    accepts = accepted_actors(history, "task-lt", 0)

    assert "transport" in accepts, f"No transport slot accepted; accepts={accepts}"
    assert accepts["transport"] == "agv-2", (
        f"Under 'longest time', agv-2 (farther) should win; got {accepts['transport']}"
    )


def test_spatial_arm_slots_resolved_by_served_by():
    """Arm slots carry served_by; the correct arm wins each arm slot."""
    topo = _topology()
    slots = derive_slots_by_actor("arm-1", "arm-2", topo)

    # Verify slot structure from derive_slots_by_actor.
    pickup = next(s for s in slots if s.slot_id == "pickup")
    place = next(s for s in slots if s.slot_id == "place")
    assert pickup.served_by == "arm-1"
    assert place.served_by == "arm-2"

    bus = NegotiationBus()
    agents = [
        _arm(bus, "arm-1", pos_px=(200.0, 200.0)),
        _arm(bus, "arm-2", pos_px=(1400.0, 200.0)),
        _agv(bus, "agv-1", pos_px=(300.0, 200.0)),
        _agv(bus, "agv-2", pos_px=(1300.0, 200.0)),
    ]
    history = _run(agents, _announce("task-arms", slots, constraint="shortest time"))
    accepts = accepted_actors(history, "task-arms", 0)

    assert accepts.get("pickup") == "arm-1", (
        f"pickup should be arm-1; got {accepts.get('pickup')}"
    )
    assert accepts.get("place") == "arm-2", (
        f"place should be arm-2; got {accepts.get('place')}"
    )
