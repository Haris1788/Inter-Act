"""End-to-end integration tests for the decentralized negotiation runtime.

A task is announced on the bus; real ArmAgent / AGVAgent instances negotiate
to consensus with no central component. Only the task announcement is
injected — every bid, accept, withdraw, and consensus event is produced by
the agents themselves.
"""

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
