"""Spatial topology model — pure data, zero I/O.

`Topology` is the single source of physical truth for actor positions in canvas
pixels. It is parsed from the optional `topology:` block in pipeline.yaml (see
`pipeline_config.get_topology()`). When that block is absent the compatibility
shim `pipeline_config.get_topology_or_legacy_shim()` synthesises a Topology
from the legacy `warehouse:` block so downstream code can always call the shim
without caring which era of config is present.

This module has NO imports of flask, yaml, os, or any I/O-capable library.
It is safe to import in unit tests that run outside the Flask app context.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional


@dataclass(frozen=True)
class ActorSpec:
    """Physical description of one actor as placed on the canvas.

    Coordinates are canvas pixels. `reach_px` is only meaningful for arms
    (the radius within which an arm can manipulate an object); AGVs ignore it.
    """

    id: str                              # e.g. "arm-1", "agv-1"
    kind: Literal["arm", "agv"]
    x: float                             # canvas px — horizontal
    y: float                             # canvas px — vertical
    reach_px: float = 120.0             # arm reach radius in px (default 3 cells @ 40 px/cell)
    capacity: int = 1
    speed: float = 1.0
    label: Optional[str] = None
    color: Optional[str] = None


@dataclass(frozen=True)
class Topology:
    """Complete spatial description of the warehouse canvas.

    `actors` is a frozen tuple so the dataclass can be frozen without issue.
    `cell_px` is the backend coarse-grid snap unit — canvas px are snapped to
    (col, row) cells for pathfinding and collision detection.
    """

    actors: tuple[ActorSpec, ...]
    canvas_width: int = 1600
    canvas_height: int = 1000
    cell_px: int = 40                    # backend coarse-grid snap unit

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    def actors_by_id(self) -> dict[str, ActorSpec]:
        """Return {actor_id: ActorSpec} mapping."""
        return {a.id: a for a in self.actors}

    def arms(self) -> tuple[ActorSpec, ...]:
        """Return only arm actors."""
        return tuple(a for a in self.actors if a.kind == "arm")

    def agvs(self) -> tuple[ActorSpec, ...]:
        """Return only AGV actors."""
        return tuple(a for a in self.actors if a.kind == "agv")

    def cell_for(self, x: float, y: float) -> tuple[int, int]:
        """Snap (x, y) canvas pixels to (col, row) coarse-grid cell."""
        return (int(x // self.cell_px), int(y // self.cell_px))

    def actor(self, actor_id: str) -> Optional[ActorSpec]:
        """Return the ActorSpec for *actor_id*, or None if unknown."""
        return self.actors_by_id().get(actor_id)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

_KIND_VALID = {"arm", "agv"}


def parse_topology(raw: dict) -> Topology:
    """Build a Topology from a parsed YAML topology block.

    Tolerant of missing / extra fields — unknown keys are silently ignored,
    missing optional keys fall back to ActorSpec/Topology defaults.

    Expected shape::

        topology:
          canvas:
            width: 1600
            height: 1000
            cell_px: 40          # optional; default 40
          actors:
            - id: "arm-1"
              kind: arm
              x: 120
              y: 300
              reach_px: 140      # optional
            - id: "agv-1"
              kind: agv
              x: 800
              y: 600

    Raises ValueError when a required field (`id`, `kind`, `x`, `y`) is
    missing or `kind` is not "arm" / "agv".
    """
    canvas_block = raw.get("canvas") or {}
    canvas_width = int(canvas_block.get("width", 1600))
    canvas_height = int(canvas_block.get("height", 1000))
    cell_px = int(canvas_block.get("cell_px", 40))

    actors_raw = raw.get("actors") or []
    actor_specs: list[ActorSpec] = []
    for i, entry in enumerate(actors_raw):
        actor_id = entry.get("id")
        if not actor_id:
            raise ValueError(f"topology actors[{i}] missing required field 'id'")
        kind = entry.get("kind")
        if kind not in _KIND_VALID:
            raise ValueError(
                f"topology actor {actor_id!r} has unknown kind {kind!r}; "
                f"must be one of {sorted(_KIND_VALID)}"
            )
        x = entry.get("x")
        y = entry.get("y")
        if x is None:
            raise ValueError(f"topology actor {actor_id!r} missing required field 'x'")
        if y is None:
            raise ValueError(f"topology actor {actor_id!r} missing required field 'y'")

        actor_specs.append(
            ActorSpec(
                id=actor_id,
                kind=kind,
                x=float(x),
                y=float(y),
                reach_px=float(entry.get("reach_px", 120.0)),
                capacity=int(entry.get("capacity", 1)),
                speed=float(entry.get("speed", 1.0)),
                label=entry.get("label"),
                color=entry.get("color"),
            )
        )

    return Topology(
        actors=tuple(actor_specs),
        canvas_width=canvas_width,
        canvas_height=canvas_height,
        cell_px=cell_px,
    )
