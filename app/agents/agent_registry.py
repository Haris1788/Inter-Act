"""Agent registry — instantiate and run one actor agent per configured actor.

Builds the live agent set from `capability.load_profiles()`: an `ArmAgent`
for every arm profile, an `AGVAgent` for every AGV profile. They all share
the process-wide negotiation bus. Stage 3 calls `start_agents()` at app
startup; Stage 2 exercises it from tests.
"""

from __future__ import annotations

import threading

from app.agents.actor_agent import ActorAgent
from app.agents.agv_agent import AGVAgent
from app.agents.arm_agent import ArmAgent
from app.agents.bus import get_bus
from app.agents.capability import load_profiles

_lock = threading.Lock()
_agents: list[ActorAgent] = []


def start_agents() -> list[ActorAgent]:
    """Build, start, and return one agent per configured actor. Idempotent:
    if agents are already running, returns the existing set."""
    global _agents
    with _lock:
        if _agents:
            return list(_agents)
        bus = get_bus()
        built: list[ActorAgent] = []
        for profile in load_profiles():
            if profile.kind == "agv":
                built.append(AGVAgent(profile, bus))
            else:
                built.append(ArmAgent(profile, bus))
        for agent in built:
            agent.start()
        _agents = built
        return list(_agents)


def stop_agents() -> None:
    """Stop every running agent and clear the registry."""
    global _agents
    with _lock:
        for agent in _agents:
            agent.stop()
        _agents = []


def get_agents() -> list[ActorAgent]:
    """The currently-running agent set."""
    with _lock:
        return list(_agents)
