"""ArmAgent — a robotic-arm actor in the decentralized negotiation runtime.

An arm fills `arm`-kind slots (pickup / place) for a station zone it can
reach. Phase 1 (Stage 2): leg execution is a recorded/simulated step; real
arm animation is wired in Stage 3.
"""

from __future__ import annotations

from typing import Optional

from app.agents.actor_agent import ActorAgent


class ArmAgent(ActorAgent):
    """Robotic-arm actor. Bids on reachable arm slots; bid score is reach
    cost (0.0 when it serves the slot's zone)."""

    def _bid_score(self, slot: dict) -> Optional[float]:
        if slot.get("kind") != "arm":
            return None
        zone = slot.get("zone")
        if zone is None or zone not in self.profile.reachable_zones:
            return None
        return 0.0
