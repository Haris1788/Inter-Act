"""AGVAgent — an Automated Guided Vehicle actor in the negotiation runtime.

An AGV fills the `transport` slot. Its bid score is the grid (Manhattan)
distance from its current cell to the pickup station's dock — with equal AGV
speed, the nearest-to-pickup AGV wins, which is goal.md's intended default.
Phase 1 (Stage 2): leg execution is a recorded/simulated step; real AGV
container commands are wired in Stage 3.
"""

from __future__ import annotations

from typing import Optional

from app.agents.actor_agent import ActorAgent


class AGVAgent(ActorAgent):
    """AGV actor. Bids on the transport slot; score = distance to pickup."""

    def _bid_score(self, slot: dict) -> Optional[float]:
        if slot.get("kind") != "agv":
            return None
        from_zone = slot.get("from_zone")
        dock = self._dock_cell(from_zone)
        if dock is None:
            return None
        col, row = self.profile.position[0], self.profile.position[1]
        return abs(col - dock[0]) + abs(row - dock[1])

    def _dock_cell(self, zone: Optional[str]) -> Optional[list[float]]:
        """The [col,row] dock cell of a station zone, from pipeline config."""
        if zone is None:
            return None
        from app.models import pipeline_config

        station = pipeline_config.get_stations().get(zone)
        if not station:
            return None
        cell = station.get("dock") or station.get("cell")
        if not cell:
            return None
        return [float(cell[0]), float(cell[1])]
