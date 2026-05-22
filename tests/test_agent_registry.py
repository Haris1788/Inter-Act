"""Unit tests for the agent registry."""

import pytest

from app.agents import agent_registry
from app.agents.arm_agent import ArmAgent
from app.agents.agv_agent import AGVAgent
from app.agents.bus import reset_bus
from app.agents.capability import CapabilityProfile


@pytest.fixture(autouse=True)
def _clean():
    reset_bus()
    agent_registry.stop_agents()
    yield
    agent_registry.stop_agents()
    reset_bus()


def test_start_agents_builds_one_agent_per_profile(monkeypatch):
    profiles = [
        CapabilityProfile(actor_id="arm-1", kind="arm",
                          reachable_zones=["input_arm"]),
        CapabilityProfile(actor_id="agv-1", kind="agv",
                          reachable_zones=["input_arm", "output_arm"]),
    ]
    monkeypatch.setattr(
        "app.agents.agent_registry.load_profiles", lambda: profiles
    )
    agents = agent_registry.start_agents()
    try:
        by_id = {a.actor_id: a for a in agents}
        assert set(by_id) == {"arm-1", "agv-1"}
        assert isinstance(by_id["arm-1"], ArmAgent)
        assert isinstance(by_id["agv-1"], AGVAgent)
        assert all(a.is_running() for a in agents)
    finally:
        agent_registry.stop_agents()


def test_stop_agents_stops_every_agent(monkeypatch):
    profiles = [
        CapabilityProfile(actor_id="arm-1", kind="arm",
                          reachable_zones=["input_arm"]),
    ]
    monkeypatch.setattr(
        "app.agents.agent_registry.load_profiles", lambda: profiles
    )
    agents = agent_registry.start_agents()
    agent_registry.stop_agents()
    assert all(not a.is_running() for a in agents)


def test_get_agents_returns_running_set(monkeypatch):
    monkeypatch.setattr(
        "app.agents.agent_registry.load_profiles", lambda: []
    )
    agent_registry.start_agents()
    assert agent_registry.get_agents() == []
