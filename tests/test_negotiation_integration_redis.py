"""Parametrized negotiation integration tests — InProcessBus and RedisStreamsBus.

Runs a representative subset of the end-to-end negotiation scenarios against
BOTH bus implementations. RedisStreamsBus is exercised against fakeredis so no
real Redis container is required.

Scenarios covered (per the Phase 4 Stage 1 spec):
- Full negotiation reaches consensus.
- Fault mid-negotiation → reopen → reconverge to a new AGV.
- Unfillable task (no AGV) → TASK_FAILED (deadline-failure).
- Higher-round halt (announce with round+1 supersedes in-flight round 0).
"""

from __future__ import annotations

import threading
import time
from typing import List

import fakeredis
import pytest

from app.agents.agv_agent import AGVAgent
from app.agents.arm_agent import ArmAgent
from app.agents.bus import InProcessBus
from app.agents.capability import CapabilityProfile
from app.agents.negotiation import Message, MessageType, accepted_actors


# ── Shared test data ───────────────────────────────────────────────────────

_STATIONS = {
    "input_arm": {"served_by": "arm-1", "cell": [1, 1], "dock": [2, 1]},
    "output_arm": {"served_by": "arm-2", "cell": [12, 1], "dock": [11, 1]},
}


@pytest.fixture(autouse=True)
def _stations(monkeypatch):
    from app.models import pipeline_config
    monkeypatch.setattr(pipeline_config, "get_stations", lambda: _STATIONS)


# ── Bus fixtures ───────────────────────────────────────────────────────────

@pytest.fixture()
def inprocess_bus():
    return InProcessBus()


@pytest.fixture()
def redis_bus():
    from app.agents.redis_bus import RedisStreamsBus

    fake_server = fakeredis.FakeServer()
    fake_client = fakeredis.FakeRedis(server=fake_server, decode_responses=True)

    bus = RedisStreamsBus.__new__(RedisStreamsBus)
    import threading as _t
    bus._redis_url = "redis://fake"
    bus._client = fake_client
    bus._subscribers = {}
    bus._subscribers_lock = _t.Lock()
    bus._seen_xids = {}
    bus._consumer_threads = []
    bus._stop_flag = _t.Event()

    yield bus

    bus.shutdown()


@pytest.fixture(params=["inprocess", "redis"])
def bus(request, inprocess_bus, redis_bus):
    if request.param == "inprocess":
        return inprocess_bus
    return redis_bus


# ── Agent helpers ──────────────────────────────────────────────────────────

def _arm(bus, actor_id, zone):
    return ArmAgent(
        CapabilityProfile(actor_id=actor_id, kind="arm", reachable_zones=[zone]),
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


def _announce(constraint="", task_id="task-1"):
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


# ── Parametrized integration tests ────────────────────────────────────────

def test_full_negotiation_reaches_consensus(bus):
    """Scenario 1: full negotiation — all three slots filled, CONSENSUS_REACHED."""
    agents = [
        _arm(bus, "arm-1", "input_arm"),
        _arm(bus, "arm-2", "output_arm"),
        _agv(bus, "agv-1", position=(3, 1)),
        _agv(bus, "agv-2", position=(10, 5)),
    ]
    history = _run(agents, _announce(constraint="shortest time"), settle=5.0)
    accepts = accepted_actors(history, "task-1", 0)
    assert accepts == {
        "pickup": "arm-1",
        "transport": "agv-1",
        "place": "arm-2",
    }, f"Unexpected accepts: {accepts}"
    assert any(m.type == MessageType.CONSENSUS_REACHED for m in history)


def test_fault_reopens_and_reconverges(bus):
    """Scenario 2: fault-reopen — faulting agv-1 triggers round 1, agv-2 wins."""
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
        # Wait for round 0 consensus.
        time.sleep(2.5)
        round0 = accepted_actors(bus.history(), "task-1", 0)
        assert round0.get("transport") == "agv-1", (
            f"Expected agv-1 in round 0 transport, got {round0}"
        )
        # Fault the winning AGV.
        agents[2].fault("injected")
        # Wait for reopen (round 1) to converge.
        time.sleep(5.0)
        round1 = accepted_actors(bus.history(), "task-1", 1)
        assert round1.get("transport") == "agv-2", (
            f"Expected agv-2 in round 1 transport, got {round1}"
        )
    finally:
        for a in agents:
            a.stop()


def test_deadline_failure_no_agv(bus):
    """Scenario 3: unfillable task (no AGV) → TASK_FAILED, no CONSENSUS_REACHED."""
    agents = [
        _arm(bus, "arm-1", "input_arm"),
        _arm(bus, "arm-2", "output_arm"),
        # No AGV agent — transport slot cannot be filled.
    ]
    history = _run(agents, _announce(), settle=5.0)
    accepts = accepted_actors(history, "task-1", 0)
    assert "transport" not in accepts, (
        f"transport slot was unexpectedly filled: {accepts}"
    )
    assert not any(m.type == MessageType.CONSENSUS_REACHED for m in history)
    assert any(m.type == MessageType.TASK_FAILED for m in history), (
        "Expected TASK_FAILED in history"
    )


def test_higher_round_supersedes(bus):
    """Scenario 4: a round+1 TASK_ANNOUNCE supersedes round 0 — agents adopt the
    new round and do NOT publish a CONSENSUS_REACHED for the stale round 0."""
    agents = [
        _arm(bus, "arm-1", "input_arm"),
        _arm(bus, "arm-2", "output_arm"),
        _agv(bus, "agv-1", position=(3, 1)),
    ]
    for a in agents:
        a.start()
    try:
        # Publish round 0.
        bus.publish(_announce(constraint="shortest time", task_id="task-hr"))
        # Immediately publish round 1 — before any bids land.
        time.sleep(0.1)
        now = time.time()
        round1_msg = Message(
            type=MessageType.TASK_ANNOUNCE,
            task_id="task-hr",
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
                "bid_deadline_ts": now + 1.5,
                "consensus_deadline_ts": now + 3.0,
            },
        )
        bus.publish(round1_msg)
        # Wait for round 1 to settle.
        time.sleep(5.0)
        history = bus.history()

        # There must be no ACCEPT for round 0 (agents should have dropped it).
        r0_accepts = accepted_actors(history, "task-hr", 0)
        # Round 1 must have reached consensus.
        r1_accepts = accepted_actors(history, "task-hr", 1)
        assert r1_accepts == {
            "pickup": "arm-1",
            "transport": "agv-1",
            "place": "arm-2",
        }, f"Round 1 accepts unexpected: {r1_accepts}"
        # No CONSENSUS_REACHED should be for round 0.
        r0_consensus = [
            m for m in history
            if m.type == MessageType.CONSENSUS_REACHED
            and m.task_id == "task-hr"
            and m.payload.get("round") == 0
        ]
        assert not r0_consensus, (
            f"CONSENSUS_REACHED emitted for stale round 0: {r0_consensus}"
        )
    finally:
        for a in agents:
            a.stop()
