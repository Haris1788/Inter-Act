"""Pure geometric helpers — no I/O, no Flask, safe to unit-test in isolation.

`dock_point` is the canonical "where on an arm's reach boundary should the
counterpart approach" projection used by `derive_slots_by_actor` to populate
arm `target_pos` and AGV `from_pos`/`to_pos`. The boundary-not-center semantic
matches the Final Goal vision: an arm picks/places on the perimeter of its
reach radius, not at its own base. An AGV docks at that same perimeter point
when the boundary faces the AGV's approach direction.
"""

from __future__ import annotations

import math
from typing import Tuple

Point = Tuple[float, float]


def dock_point(anchor: Point, toward: Point, reach_px: float) -> Point:
    """Project `anchor` onto its reach circle along the vector `anchor → toward`.

    Returns the point at distance `reach_px` from `anchor` lying on the line
    `anchor → toward`. Safe for collinear and degenerate inputs:

      * If `toward == anchor`, returns `anchor` unchanged (no direction).
      * If `reach_px <= 0`, returns `anchor` unchanged.

    Both inputs and output are canvas pixels.

    Args:
        anchor:   the actor whose reach circle is being projected (e.g. an arm).
        toward:   the point the projection should face (e.g. partner arm, zone).
        reach_px: radius of the reach circle, in canvas pixels.

    Returns:
        (x, y) point on the reach circle of `anchor` along the anchor→toward
        ray. When the inputs are degenerate, returns `anchor`.
    """
    if reach_px <= 0:
        return anchor
    ax, ay = anchor
    tx, ty = toward
    dx, dy = tx - ax, ty - ay
    dist = math.hypot(dx, dy)
    if dist == 0.0:
        return anchor
    inv = reach_px / dist
    return (ax + dx * inv, ay + dy * inv)
