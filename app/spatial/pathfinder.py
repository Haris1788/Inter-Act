"""A* pathfinder on a 4-connected coarse grid.

Pure module — no I/O, no global state, no imports of Flask or Redis.
Safe to call from any thread without locking.

``Cell = (col, row)`` — column-first to match topology.cell_for convention.

Tie-breaking:
    When two nodes share the same f-score, the one with the lexicographically
    smaller (col, row) is expanded first.  This produces a deterministic path
    for any given (start, goal, blocked) triple regardless of Python dict
    ordering or hash randomisation.  The counter in the heap entry achieves
    this: each pushed node gets a monotonically increasing sequence number so
    the heap never falls back to comparing ``Cell`` tuples directly (which
    would work on ints but is still fragile).  Instead the counter itself
    acts as the second level of ordering — but because we want lex (col, row)
    as the tiebreak we push with ``(f, col, row, counter, cell)`` so the heap
    first breaks ties by col, then row, before ever using the counter.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Iterable, Optional

Cell = tuple[int, int]


@dataclass(frozen=True)
class Grid:
    """Immutable coarse grid description.

    ``obstacles`` is a ``frozenset`` of cells that are permanently blocked
    (e.g. arm pivot cells).  Dynamic reservations held by other actors are
    passed separately as the ``blocked`` argument to ``a_star``.
    """

    cols: int
    rows: int
    obstacles: frozenset[Cell] = field(default_factory=frozenset)

    def in_bounds(self, c: Cell) -> bool:
        """True when *c* is within the grid boundaries."""
        col, row = c
        return 0 <= col < self.cols and 0 <= row < self.rows

    def passable(self, c: Cell, ignore: Iterable[Cell] = ()) -> bool:
        """True when *c* is in-bounds and not an obstacle.

        ``ignore`` is a collection of obstacle cells to treat as passable
        (used when the start or goal coincides with a recorded obstacle).
        """
        if not self.in_bounds(c):
            return False
        if c in self.obstacles:
            return c in set(ignore)
        return True

    def neighbours(self, c: Cell) -> list[Cell]:
        """Return the 4-connected in-bounds neighbours of *c* (not filtered by obstacles)."""
        col, row = c
        candidates = [(col - 1, row), (col + 1, row), (col, row - 1), (col, row + 1)]
        return [n for n in candidates if self.in_bounds(n)]


# ---------------------------------------------------------------------------
# A* implementation
# ---------------------------------------------------------------------------

def _manhattan(a: Cell, b: Cell) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def a_star(
    grid: Grid,
    start: Cell,
    goal: Cell,
    blocked: Iterable[Cell] = (),
) -> Optional[list[Cell]]:
    """Return a path ``[start, ..., goal]`` (inclusive) or ``None`` if unreachable.

    Parameters
    ----------
    grid:
        The ``Grid`` describing the warehouse canvas (obstacles = arm pivots).
    start:
        Starting cell ``(col, row)``.
    goal:
        Target cell ``(col, row)``.
    blocked:
        Additional cells to treat as impassable during this search — typically
        the cells reserved by other actors (NOT the calling actor's own cells).

    Tie-breaking:
        Equal f-score nodes are ordered by lex ``(col, row)`` — deterministic
        across 100 identical calls with the same arguments.
    """
    if start == goal:
        return [start]

    blocked_set: frozenset[Cell] = frozenset(blocked)
    # Start and goal must be passable; ignore them in the obstacle check so
    # that a goal that coincides with a recorded-but-cleared obstacle still works.
    ignore_for_passable = {start, goal}

    def _passable(c: Cell) -> bool:
        if c in blocked_set and c not in ignore_for_passable:
            return False
        return grid.passable(c, ignore=ignore_for_passable)

    if not _passable(start) or not _passable(goal):
        return None

    # g_score[c] = cost of cheapest path from start to c found so far.
    g_score: dict[Cell, int] = {start: 0}
    came_from: dict[Cell, Cell] = {}

    # Heap entries: (f, col, row, cell) — col/row provide lex tie-break.
    open_heap: list[tuple[int, int, int, Cell]] = []
    heapq.heappush(open_heap, (_manhattan(start, goal), start[0], start[1], start))
    in_open: set[Cell] = {start}

    while open_heap:
        _, _, _, current = heapq.heappop(open_heap)

        if current == goal:
            # Reconstruct path.
            path: list[Cell] = []
            node: Cell = goal
            while node in came_from:
                path.append(node)
                node = came_from[node]
            path.append(start)
            path.reverse()
            return path

        in_open.discard(current)
        current_g = g_score[current]

        for neighbour in grid.neighbours(current):
            if not _passable(neighbour):
                continue
            tentative_g = current_g + 1  # uniform cost grid
            if tentative_g < g_score.get(neighbour, 10**9):
                came_from[neighbour] = current
                g_score[neighbour] = tentative_g
                f = tentative_g + _manhattan(neighbour, goal)
                heapq.heappush(open_heap, (f, neighbour[0], neighbour[1], neighbour))
                in_open.add(neighbour)

    return None  # goal unreachable


# ---------------------------------------------------------------------------
# Topology -> Grid bridge
# ---------------------------------------------------------------------------

def grid_from_topology(topology, extra_obstacles: Iterable[Cell] = ()) -> "Grid":
    """Build a ``Grid`` from a ``Topology`` instance.

    Arm pivot cells are marked as obstacles — an AGV cannot pass through an
    arm's pivot cell (it docks adjacent to the arm, not on the pivot itself).
    AGVs are NOT obstacles; they are mobile and their positions are passed as
    ``blocked`` to ``a_star`` at call time.

    Parameters
    ----------
    topology:
        An ``app.models.topology.Topology`` instance.
    extra_obstacles:
        Additional cells to treat as obstacles.  The caller can pass cells
        held by other actors to widen the static obstacle set if desired;
        however the preferred pattern is to pass those as ``blocked`` to
        ``a_star`` so they do not permanently pollute the Grid.
    """
    cell_px: int = topology.cell_px
    cols: int = topology.canvas_width // cell_px
    rows: int = topology.canvas_height // cell_px

    arm_cells: set[Cell] = set()
    for spec in topology.arms():
        cell = topology.cell_for(spec.x, spec.y)
        arm_cells.add(cell)

    obstacles: frozenset[Cell] = frozenset(arm_cells | set(extra_obstacles))
    return Grid(cols=cols, rows=rows, obstacles=obstacles)
