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
_started = False


def start_agents() -> list[ActorAgent]:
    """Build, start, and return one agent per configured actor. Idempotent:
    once started, returns the existing set until stop_agents() is called."""
    global _agents, _started
    with _lock:
        if _started:
            return list(_agents)
        _started = True
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
    """Stop every running agent and clear the registry. The agent threads are
    joined OUTSIDE the registry lock so a slow join cannot stall other callers
    (e.g. Flask shutdown)."""
    global _agents, _started
    with _lock:
        to_stop = _agents
        _agents = []
        _started = False
    for agent in to_stop:
        agent.stop()


def get_agents() -> list[ActorAgent]:
    """The currently-running agent set."""
    with _lock:
        return list(_agents)
