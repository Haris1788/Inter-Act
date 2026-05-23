# Phase 5 Stage 7 Committed Sub-Spec — Pathfinding + Collision + Reservation

A* pathfinder + epoch-scoped per-cell Redis reservation + deterministic
deadlock prevention. Single track — consistency surface. NO parallel.

## Decisions (approved + user refinement)

- Pathfinder: A* on 4-connected grid, Manhattan heuristic.
- Reservation key: **`(task_id, actor_id, path_epoch, cell_id)`** — full
  tuple. `path_epoch` is a monotonic counter per `(task_id, actor_id)`
  bumped on every replan. Bare `cell_id` keys are forbidden — they create
  ghost occupancy across replans/recovery.
- Deadlock: deterministic cell-order tie-break (lex `(col, row)`) +
  timeout-replan after T.

## Tracks

A (single track, blocking): pathfinder + reservation primitives + tests.
B (after A): integrate into `AGVAgent._execute_leg` replacing straight-line.

### Track A — Primitives

#### File: `app/spatial/__init__.py` (new)
Empty package init.

#### File: `app/spatial/pathfinder.py` (new)
```python
from dataclasses import dataclass
from typing import Iterable, Optional

Cell = tuple[int, int]

@dataclass(frozen=True)
class Grid:
    cols: int
    rows: int
    obstacles: frozenset[Cell] = frozenset()

    def in_bounds(self, c: Cell) -> bool: ...
    def passable(self, c: Cell, ignore: Iterable[Cell] = ()) -> bool: ...
    def neighbours(self, c: Cell) -> list[Cell]: ...   # 4-connected

def a_star(
    grid: Grid,
    start: Cell,
    goal: Cell,
    blocked: Iterable[Cell] = (),
) -> Optional[list[Cell]]:
    """Return list of cells start→goal (inclusive) or None if unreachable.

    Manhattan heuristic. Tie-broken by lex (col, row) for determinism.
    `blocked` = cells reserved by other actors (not self).
    """
    ...

def grid_from_topology(topology, extra_obstacles=()) -> Grid:
    """Build a Grid from canvas dims + cell_px + actor pivot cells.

    Arms' pivot cells become obstacles (AGVs can dock adjacent, not on top).
    extra_obstacles: caller can add reservation cells held by other actors.
    """
    ...
```

Pure: no I/O. Returns cell list. Caller handles motion. Worst case at 50×50
grid is ~2500 nodes — well under 10 ms in CPython.

#### File: `app/spatial/reservation.py` (new)
```python
from dataclasses import dataclass
from typing import Iterable, Optional
import threading
import time

@dataclass
class Reservation:
    task_id: str
    actor_id: str
    epoch: int                 # monotonic per (task_id, actor_id)
    cells: list[tuple[int,int]]
    expires_at: float

class ReservationStore:
    """Redis-backed per-cell reservation with epoch scoping.

    Key format:
      resv:task:<task_id>:actor:<actor_id>:epoch:<E>:cell:<col>:<row>
    Value: "1" (presence-only). TTL = ttl_ms.

    No bare cell keys. Ownership is keyed by the full tuple.

    Helper functions:
      next_epoch(task_id, actor_id)            -> int (Redis INCR counter)
      reserve(task_id, actor_id, epoch, cells, ttl_ms) -> bool
      release(task_id, actor_id, epoch)        -> int (deleted count via SCAN)
      held_by_others(task_id, exclude_actor)   -> set[Cell]
      touch(task_id, actor_id, epoch, ttl_ms)  -> bool   (renew TTL)
    """
    def __init__(self, redis_client): ...
    ...

# Module-level dual-backend (inmemory + redis) just like agv_state etc.
_store = ...
def get_store() -> ReservationStore: ...

class PathKeepalive:
    """Daemon thread renewing reservation TTL every interval_s. Mirrors
    LockKeepalive (Phase 4 Stage 2)."""
    ...
```

In-memory backend for tests + Redis backend for prod. Selector via
`INTERACT_STATE` env (same pattern as Phase 4 Stage 2).

#### Tests
- `tests/test_pathfinder.py`:
  - `test_straight_line_no_obstacles` — start to goal returns the expected count.
  - `test_obstacle_in_path_is_routed_around` — single-cell obstacle.
  - `test_unreachable_returns_none` — goal walled off.
  - `test_blocked_cells_avoided` — `blocked` arg respected (not obstacles).
  - `test_determinism_under_ties` — same input → same output across 100 runs.
  - `test_grid_from_topology_marks_arm_pivots` — arms become obstacles.
- `tests/test_reservation_store.py` (both backends parametrized via fakeredis):
  - `test_reserve_idempotent_same_owner` — same (task, actor, epoch, cell) OK.
  - `test_reserve_conflict_different_owner` — different actor at same epoch → False.
  - `test_release_by_epoch_garbage_collects` — `release(task, actor, E)` deletes ALL `epoch:E:` keys for that pair via SCAN.
  - `test_next_epoch_monotonic_per_pair` — independent counters per `(task, actor)`.
  - `test_held_by_others_filters_self` — excludes own actor's reservations.
  - `test_ttl_expiry_clears_cells` — short TTL + sleep → cells free.
  - `test_touch_renews_ttl` — `touch()` extends.
  - `test_path_keepalive_renews_until_stopped`.

### Track B — Integrate into AGVAgent

After Track A's primitives land + tests pass, dispatch Track B as a follow-up
(separate spec — written after A reports).

Track B replaces `_traverse_to` straight-line steps with:
1. `epoch = next_epoch(task_id, agv_id)`.
2. `obstacles_others = held_by_others(task_id, agv_id)`.
3. `path = a_star(grid, current_cell, target_cell, blocked=obstacles_others)`.
4. On `path is None` → wait 200 ms, replan up to 5 times; then fault.
5. `reserve(task_id, agv_id, epoch, path, ttl_ms=180_000)`.
6. Start `PathKeepalive` for TTL renewal.
7. Traverse cells (px interpolation per Stage 5 between cell centers).
8. On collision detected (another AGV on next cell that wasn't there at
   reserve time): cancel reservation, bump epoch, replan.
9. After traverse: release current epoch's cells.

This is the integration-only part; Track B's spec gets written once Track A
reports.

## Hard invariants (Track A)

- Pathfinder is pure: no I/O, no global state.
- Reservation keys ALWAYS include `task_id` + `actor_id` + `epoch` + `cell`.
  Bare cell keys are a bug.
- `release(task, actor, epoch)` cleans by SCAN-by-prefix — no per-cell
  bookkeeping required by caller.
- `held_by_others` filters by `actor_id` mismatch — self-owned reservations
  never block self.
- Deterministic tie-breaking in A* by lex (col, row).
- 567 baseline tests pass UNCHANGED.

## Verify

- `python -m pytest tests/test_pathfinder.py tests/test_reservation_store.py -q` — all pass.
- Full suite: ≥ 567 + ~14 new / 5. Zero regressions.
- New tests pass 3× consecutively.
- `python -c "from app.spatial.pathfinder import a_star, Grid; print(a_star(Grid(10,10), (0,0), (5,5)))"` smoke test.

## Opus review gate

- Epoch keys verified at integration time — no caller anywhere uses a bare
  cell key.
- A* determinism under tied paths.
- Reservation SCAN-by-prefix cleanup actually deletes everything (not just
  one cell).
- Dual-backend (in-memory + Redis) parametrized tests both pass.
