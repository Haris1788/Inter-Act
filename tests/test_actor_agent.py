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
