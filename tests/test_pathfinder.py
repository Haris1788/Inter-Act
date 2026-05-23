"""Unit tests for app.spatial.pathfinder.

Six tests per spec:
1. test_straight_line_no_obstacles
2. test_obstacle_in_path_is_routed_around
3. test_unreachable_returns_none
4. test_blocked_cells_avoided
5. test_determinism_under_ties
6. test_grid_from_topology_marks_arm_pivots
"""

from __future__ import annotations

import pytest

from app.spatial.pathfinder import Grid, Cell, a_star, grid_from_topology


# ---------------------------------------------------------------------------
# 1. Straight-line path (no obstacles)
# ---------------------------------------------------------------------------

class TestStraightLine:
    def test_straight_line_no_obstacles(self):
        grid = Grid(cols=10, rows=10)
        path = a_star(grid, (0, 0), (5, 0))
        # Manhattan path of length 6 along row 0.
        assert path is not None
        assert path[0] == (0, 0)
        assert path[-1] == (5, 0)
        assert len(path) == 6  # 6 cells inclusive

    def test_start_equals_goal(self):
        grid = Grid(cols=5, rows=5)
        path = a_star(grid, (2, 2), (2, 2))
        assert path == [(2, 2)]


# ---------------------------------------------------------------------------
# 2. Obstacle in path is routed around
# ---------------------------------------------------------------------------

class TestObstacleRouted:
    def test_obstacle_in_path_is_routed_around(self):
        # Single obstacle at (2, 0) forces path to detour via (2, 1).
        obstacles: frozenset[Cell] = frozenset({(2, 0)})
        grid = Grid(cols=6, rows=3, obstacles=obstacles)
        path = a_star(grid, (0, 0), (4, 0))
        assert path is not None
        assert (2, 0) not in path
        assert path[0] == (0, 0)
        assert path[-1] == (4, 0)


# ---------------------------------------------------------------------------
# 3. Unreachable goal returns None
# ---------------------------------------------------------------------------

class TestUnreachable:
    def test_unreachable_returns_none(self):
        # Wall off the goal at (4, 0) by surrounding it with obstacles.
        # Goal itself is an obstacle — unreachable.
        obstacles: frozenset[Cell] = frozenset({(4, 0), (3, 0), (4, 1)})
        grid = Grid(cols=6, rows=2, obstacles=obstacles)
        # Start (0, 0) to goal (4, 0) — goal is obstacle AND surrounded.
        # But goal == obstacle means passable(..., ignore={start,goal}) lets us reach
        # the goal; we need to make the goal fully enclosed to get None.
        # Use a 1×1 grid with goal == obstacle to guarantee unreachable.
        obstacles2: frozenset[Cell] = frozenset({(3, 0), (1, 0), (2, 1), (2, 0)})
        grid2 = Grid(cols=5, rows=3, obstacles=obstacles2)
        path = a_star(grid2, (0, 0), (2, 0))
        # (2,0) is obstacle; its neighbours (3,0) and (1,0) are also obstacles;
        # (2,1) is also obstacle — the goal cell is walled off; path must be None.
        assert path is None

    def test_unreachable_completely_blocked_goal(self):
        """Minimal case: goal on a 2-cell grid where both cells are obstacles."""
        # Grid 2×1, goal=(1,0) is an obstacle, start=(0,0) is also an obstacle
        # but start/goal are ignored in passable check — goal is surrounded by
        # out-of-bounds so it's a dead-end.  Only 2 cells; start→goal is (0,0)→(1,0).
        # Surround goal so it truly cannot be reached from non-start cells.
        # Use a grid where goal (3,1) is a complete peninsula: only access via (3,0)
        # which is also an obstacle.
        obstacles: frozenset[Cell] = frozenset({(3, 0)})
        grid = Grid(cols=4, rows=2, obstacles=obstacles)
        # goal (3,1) neighbours are (2,1), (3,0) [obstacle]. (2,1) is passable.
        # So actually reachable via (2,1). Let's use a proper walled-off case:
        obstacles2: frozenset[Cell] = frozenset({(1, 0), (0, 1)})
        grid2 = Grid(cols=2, rows=2, obstacles=obstacles2)
        # (1,1) accessible from (0,0)? (0,0)→(0,1)[blocked] or (1,0)[blocked] — None.
        path = a_star(grid2, (0, 0), (1, 1))
        assert path is None


# ---------------------------------------------------------------------------
# 4. Blocked cells (dynamic reservation) are avoided
# ---------------------------------------------------------------------------

class TestBlockedCells:
    def test_blocked_cells_avoided(self):
        # Clear grid but block (2, 0) via the `blocked` arg (not an obstacle).
        grid = Grid(cols=6, rows=3)
        path = a_star(grid, (0, 0), (4, 0), blocked=[(2, 0)])
        assert path is not None
        assert (2, 0) not in path
        assert path[0] == (0, 0)
        assert path[-1] == (4, 0)

    def test_blocked_does_not_affect_grid_obstacles(self):
        """blocked arg should not modify the Grid; Grid.obstacles is unchanged."""
        obstacles: frozenset[Cell] = frozenset({(1, 0)})
        grid = Grid(cols=5, rows=3, obstacles=obstacles)
        _ = a_star(grid, (0, 0), (4, 0), blocked=[(2, 0)])
        # Ensure obstacles attribute is unchanged.
        assert grid.obstacles == frozenset({(1, 0)})


# ---------------------------------------------------------------------------
# 5. Determinism under ties
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_determinism_under_ties(self):
        """Same (grid, start, goal, blocked) → same path across 100 calls."""
        grid = Grid(cols=8, rows=8)
        start: Cell = (0, 0)
        goal: Cell = (4, 4)
        paths = [a_star(grid, start, goal) for _ in range(100)]
        assert all(p == paths[0] for p in paths), (
            "a_star returned different paths across calls with identical inputs"
        )
        # Also verify start/end.
        assert paths[0] is not None
        assert paths[0][0] == start
        assert paths[0][-1] == goal

    def test_determinism_with_obstacles(self):
        """Deterministic even when obstacles create multiple equal-length detours."""
        obstacles: frozenset[Cell] = frozenset({(2, 1), (2, 2), (2, 3)})
        grid = Grid(cols=6, rows=6, obstacles=obstacles)
        start: Cell = (0, 2)
        goal: Cell = (4, 2)
        paths = [a_star(grid, start, goal) for _ in range(100)]
        assert all(p == paths[0] for p in paths)


# ---------------------------------------------------------------------------
# 6. grid_from_topology marks arm pivot cells as obstacles
# ---------------------------------------------------------------------------

class TestGridFromTopology:
    def _make_topology(self, actors, canvas_width=400, canvas_height=400, cell_px=40):
        """Build a minimal Topology-like object."""
        from app.models.topology import Topology, ActorSpec
        actor_specs = tuple(
            ActorSpec(
                id=a["id"],
                kind=a["kind"],
                x=float(a["x"]),
                y=float(a["y"]),
            )
            for a in actors
        )
        return Topology(
            actors=actor_specs,
            canvas_width=canvas_width,
            canvas_height=canvas_height,
            cell_px=cell_px,
        )

    def test_grid_from_topology_marks_arm_pivots(self):
        """Arm pivot cells appear in Grid.obstacles; AGV cells do NOT."""
        topology = self._make_topology([
            {"id": "arm-1", "kind": "arm", "x": 120.0, "y": 80.0},   # cell (3,2)
            {"id": "arm-2", "kind": "arm", "x": 200.0, "y": 160.0},  # cell (5,4)
            {"id": "agv-1", "kind": "agv", "x": 40.0, "y": 40.0},   # cell (1,1) — NOT obstacle
        ])
        grid = grid_from_topology(topology)
        # Grid dimensions: 400/40=10 cols, 400/40=10 rows.
        assert grid.cols == 10
        assert grid.rows == 10
        # Arm pivot cells are obstacles.
        assert (3, 2) in grid.obstacles   # arm-1: 120//40=3, 80//40=2
        assert (5, 4) in grid.obstacles   # arm-2: 200//40=5, 160//40=4
        # AGV cell is NOT an obstacle.
        assert (1, 1) not in grid.obstacles

    def test_grid_from_topology_extra_obstacles(self):
        """extra_obstacles are merged into Grid.obstacles."""
        topology = self._make_topology([
            {"id": "arm-1", "kind": "arm", "x": 40.0, "y": 40.0},
        ])
        extra: list[Cell] = [(7, 7), (8, 8)]
        grid = grid_from_topology(topology, extra_obstacles=extra)
        assert (7, 7) in grid.obstacles
        assert (8, 8) in grid.obstacles

    def test_grid_from_topology_cell_size(self):
        """Grid.cols and Grid.rows follow canvas_width/height / cell_px."""
        topology = self._make_topology([], canvas_width=1600, canvas_height=1000, cell_px=40)
        grid = grid_from_topology(topology)
        assert grid.cols == 40
        assert grid.rows == 25
