"""Unit tests for the ActorAgent base class."""

import time

import pytest

from app.agents.actor_agent import ActorAgent, AgentState
from app.agents.bus import NegotiationBus
from app.agents.capability import CapabilityProfile
from app.agents.negotiation import Message, MessageType


def _profile(actor_id="arm-1", kind="arm"):
    return CapabilityProfile(
        actor_id=actor_id, kind=kind, reachable_zones=["input_arm"]
    )


def test_agent_starts_idle_and_stops_cleanly():
    bus = NegotiationBus()
    agent = ActorAgent(_profile(), bus)
    assert agent.state == AgentState.IDLE
    agent.start()
    try:
        assert agent.is_running()
    finally:
        agent.stop()
    assert not agent.is_running()


def test_agent_subscribes_and_receives_bus_messages():
    bus = NegotiationBus()
    agent = ActorAgent(_profile(), bus)
    agent.start()
    try:
        bus.publish(
            Message(
                type=MessageType.AVAILABILITY_UPDATE,
                task_id="t1",
                negotiation_round=0,
                sender="agv-9",
            )
        )
        deadline = time.time() + 2.0
        while time.time() < deadline and agent.messages_seen() == 0:
            time.sleep(0.02)
        assert agent.messages_seen() >= 1
    finally:
        agent.stop()


def test_agent_ignores_its_own_messages():
    bus = NegotiationBus()
    agent = ActorAgent(_profile("arm-1"), bus)
    agent.start()
    try:
        bus.publish(
            Message(
                type=MessageType.AVAILABILITY_UPDATE,
                task_id="t1",
                negotiation_round=0,
                sender="arm-1",
            )
        )
        time.sleep(0.3)
        assert agent.messages_seen() == 0
    finally:
        agent.stop()


def _announce(round_=0, constraint="", task_id="t1"):
    now = time.time()
    return Message(
        type=MessageType.TASK_ANNOUNCE,
        task_id=task_id,
        negotiation_round=round_,
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


class _ScoringAgent(ActorAgent):
    """Test agent that bids a fixed score on every slot whose kind matches."""

    def __init__(self, profile, bus, score=5.0):
        super().__init__(profile, bus)
        self._score = score

    def _bid_score(self, slot: dict):
        if slot["kind"] == self.profile.kind:
            return self._score
        return None


def _published(bus, mtype, sender):
    return [m for m in bus.history() if m.type == mtype and m.sender == sender]


def test_task_announce_makes_agent_broadcast_capability():
    bus = NegotiationBus()
    agent = _ScoringAgent(_profile("arm-1", "arm"), bus)
    agent.start()
    try:
        bus.publish(_announce())
        time.sleep(0.3)
        caps = _published(bus, MessageType.CAPABILITY_BROADCAST, "arm-1")
        assert len(caps) == 1
        assert caps[0].payload["actor_id"] == "arm-1"
    finally:
        agent.stop()


def test_task_announce_makes_capable_agent_bid_and_enter_bidding():
    bus = NegotiationBus()
    agent = _ScoringAgent(_profile("arm-1", "arm"), bus)
    agent.start()
    try:
        bus.publish(_announce())
        time.sleep(0.3)
        bids = _published(bus, MessageType.BID, "arm-1")
        assert {b.slot_id for b in bids} == {"pickup", "place"}
        assert agent.state == AgentState.BIDDING
    finally:
        agent.stop()


def test_agent_that_cannot_fill_any_slot_does_not_bid():
    bus = NegotiationBus()
    agent = ActorAgent(_profile("arm-1", "arm"), bus)
    agent.start()
    try:
        bus.publish(_announce())
        time.sleep(0.3)
        assert _published(bus, MessageType.BID, "arm-1") == []
        assert agent.state == AgentState.IDLE
    finally:
        agent.stop()


def test_agent_accepts_slot_it_wins_at_bid_deadline():
    bus = NegotiationBus()
    a1 = _ScoringAgent(_profile("arm-1", "arm"), bus, score=3.0)
    a2 = _ScoringAgent(_profile("arm-2", "arm"), bus, score=9.0)
    a1.start()
    a2.start()
    try:
        bus.publish(_announce(constraint="shortest time"))
        time.sleep(2.2)  # past the 1.5s bid deadline
        a1_accepts = _published(bus, MessageType.ACCEPT, "arm-1")
        a2_withdraws = _published(bus, MessageType.WITHDRAW, "arm-2")
        assert {m.slot_id for m in a1_accepts} == {"pickup", "place"}
        assert {m.slot_id for m in a2_withdraws} == {"pickup", "place"}
        assert a1.state == AgentState.WON
    finally:
        a1.stop()
        a2.stop()


def test_agent_evaluates_only_once_per_round():
    bus = NegotiationBus()
    a1 = _ScoringAgent(_profile("arm-1", "arm"), bus, score=3.0)
    a1.start()
    try:
        bus.publish(_announce())
        time.sleep(2.5)
        accepts = _published(bus, MessageType.ACCEPT, "arm-1")
        assert len(accepts) == 2
    finally:
        a1.stop()
