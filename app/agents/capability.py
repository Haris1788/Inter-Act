"""Capability profiles — what each actor independently knows about itself.

Each actor owns its profile and broadcasts it on the bus. There is no central
capability registry: discovery emerges from `capability_broadcast` messages.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CapabilityProfile:
    """One actor's self-knowledge: which zones it reaches, where it is, how
    fast, how much it carries."""

    actor_id: str
    kind: str  # "arm" | "agv"
    reachable_zones: list[str] = field(default_factory=list)
    position: list[float] = field(default_factory=lambda: [0.0, 0.0])
    speed: float = 1.0
    capacity: int = 1

    def to_dict(self) -> dict:
        """JSON-safe dict — the payload of a `capability_broadcast` message."""
        return {
            "actor_id": self.actor_id,
            "kind": self.kind,
            "reachable_zones": list(self.reachable_zones),
            "position": list(self.position),
            "speed": self.speed,
            "capacity": self.capacity,
        }
