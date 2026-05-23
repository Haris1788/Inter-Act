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
    # Phase 5 Stage 1 — spatial fields (optional for back-compat; all existing
    # constructors keep working because both fields have defaults).
    position_px: tuple[float, float] = (0.0, 0.0)
    reach_px: float = 120.0

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict — the payload of a `capability_broadcast` message."""
        return {
            "actor_id": self.actor_id,
            "kind": self.kind,
            "reachable_zones": list(self.reachable_zones),
            "position": list(self.position),
            "speed": self.speed,
            "capacity": self.capacity,
            "position_px": list(self.position_px),
            "reach_px": self.reach_px,
        }

    @classmethod
    def from_topology_spec(cls, spec) -> "CapabilityProfile":
        """Build a CapabilityProfile directly from a topology ActorSpec.

        ``spec`` is an ``app.models.topology.ActorSpec`` instance. The import
        is done lazily inside the method to avoid a circular import at module
        load time (capability <- topology <- pipeline_config would be circular
        if topology imported capability).

        Stage 3 will wire `load_profiles()` to call this; in Stage 1 it is
        available but not yet invoked by the runtime.
        """
        return cls(
            actor_id=spec.id,
            kind=spec.kind,
            reachable_zones=[],          # Stage 3 populates via station mapping
            position=[spec.x, spec.y],  # coarse grid position (px here)
            speed=spec.speed,
            capacity=spec.capacity,
            position_px=(spec.x, spec.y),
            reach_px=spec.reach_px,
        )


def load_profiles() -> list[CapabilityProfile]:
    """Build one CapabilityProfile per actor from pipeline.yaml.

    Stage 3: when a `topology:` block is present, builds profiles from the
    Topology directly via `from_topology_spec`. Arms get `reachable_zones =
    [actor_id]` so Phase 4's zone-match `_bid_score` (which reads slot["zone"])
    still pins a slot to its served-by arm. AGVs get the list of all arm ids as
    their reachable zones (they travel the full topology).

    Falls back to the legacy warehouse path when only `warehouse:` is present:
    Arms: one per `arms:` entry; reachable zones from `served_by` station
    mappings. AGVs: one per `warehouse.agvs` entry; they reach every station.
    """
    from app.models import pipeline_config

    # Stage 3 topology path — takes precedence when topology block is present.
    topology = pipeline_config.get_topology()
    if topology is not None:
        profiles: list[CapabilityProfile] = []
        arm_ids = [a.id for a in topology.arms()]
        for spec in topology.actors:
            profile = CapabilityProfile.from_topology_spec(spec)
            if spec.kind == "arm":
                # Own id as zone so Phase 4 zone-match pins pickup/place slots.
                profile.reachable_zones = [spec.id]
            else:
                # AGVs reach all arm zones (transport between any two arms).
                profile.reachable_zones = list(arm_ids)
            profiles.append(profile)
        return profiles

    # Legacy warehouse path (unchanged from Phase 4).
    stations = pipeline_config.get_stations()

    # arm id -> the station zone(s) it serves
    arm_zones: dict[str, list[str]] = {}
    for station_name, station in stations.items():
        served = station.get("served_by")
        if served:
            arm_zones.setdefault(served, []).append(station_name)

    profiles = []

    # Resolve cell_px for position_px calculation from the warehouse grid.
    # Falls back to 40 if no grid config is available.
    try:
        grid_cfg = pipeline_config.get_grid()
        cell_px = float(grid_cfg.get("cell_px", 40)) if grid_cfg else 40.0
    except Exception:
        cell_px = 40.0

    for arm in pipeline_config.get_arms():
        actor_id = f"arm-{arm['id']}"
        # Derive position_px from the arm's station dock cell when available.
        arm_position_px: tuple[float, float] = (0.0, 0.0)
        for _station_name, station in stations.items():
            if station.get("served_by") == actor_id:
                dock = station.get("dock") or station.get("cell")
                if dock and len(dock) >= 2:
                    arm_position_px = (float(dock[0]) * cell_px, float(dock[1]) * cell_px)
                break
        profiles.append(
            CapabilityProfile(
                actor_id=actor_id,
                kind="arm",
                reachable_zones=arm_zones.get(actor_id, []),
                speed=1.0,
                capacity=1,
                position_px=arm_position_px,
            )
        )

    all_station_zones = list(stations.keys())
    for agv in pipeline_config.get_agvs():
        bay = agv.get("bay", [0, 0])
        # Populate position_px from bay cell × cell_px for spatial bidding.
        bay_position_px: tuple[float, float] = (
            float(bay[0]) * cell_px,
            float(bay[1]) * cell_px,
        )
        profiles.append(
            CapabilityProfile(
                actor_id=agv["id"],
                kind="agv",
                reachable_zones=list(all_station_zones),
                position=[float(bay[0]), float(bay[1])],
                speed=float(agv.get("speed", 1.0)) or 1.0,
                capacity=int(agv.get("capacity", 1)),
                position_px=bay_position_px,
            )
        )

    return profiles
