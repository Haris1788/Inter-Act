"""Capability profiles — what each actor independently knows about itself.

Each actor owns its profile and broadcasts it on the bus. There is no central
capability registry: discovery emerges from `capability_broadcast` messages.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class CapabilityProfile:
    """One actor's self-knowledge: which zones it reaches, where it is, how
    fast, how much it carries."""

    actor_id: str
    kind: Literal["arm", "agv"]
    reachable_zones: list[str] = field(default_factory=list)
    position: list[float] = field(default_factory=lambda: [0.0, 0.0])
    speed: float = 1.0
    capacity: int = 1

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict — the payload of a `capability_broadcast` message."""
        return {
            "actor_id": self.actor_id,
            "kind": self.kind,
            "reachable_zones": list(self.reachable_zones),
            "position": list(self.position),
            "speed": self.speed,
            "capacity": self.capacity,
        }


def load_profiles() -> list[CapabilityProfile]:
    """Build one CapabilityProfile per actor from pipeline.yaml.

    Arms: one per `arms:` entry; reachable zones are the station(s) that name
    the arm in `served_by`. AGVs: one per `warehouse.agvs` entry; they reach
    every station (they travel the lane between docks); position is the bay
    cell. AGV speed/capacity are not in config today, so both default — equal
    speed makes distance the bid differentiator (goal.md's "nearest" default).
    """
    from app.models import pipeline_config

    stations = pipeline_config.get_stations()

    # arm id -> the station zone(s) it serves
    arm_zones: dict[str, list[str]] = {}
    for station_name, station in stations.items():
        served = station.get("served_by")
        if served:
            arm_zones.setdefault(served, []).append(station_name)

    profiles: list[CapabilityProfile] = []

    for arm in pipeline_config.get_arms():
        actor_id = f"arm-{arm['id']}"
        profiles.append(
            CapabilityProfile(
                actor_id=actor_id,
                kind="arm",
                reachable_zones=arm_zones.get(actor_id, []),
                speed=1.0,
                capacity=1,
            )
        )

    all_station_zones = list(stations.keys())
    for agv in pipeline_config.get_agvs():
        bay = agv.get("bay", [0, 0])
        profiles.append(
            CapabilityProfile(
                actor_id=agv["id"],
                kind="agv",
                reachable_zones=list(all_station_zones),
                position=[float(bay[0]), float(bay[1])],
                speed=float(agv.get("speed", 1.0)) or 1.0,
                capacity=int(agv.get("capacity", 1)),
            )
        )

    return profiles
