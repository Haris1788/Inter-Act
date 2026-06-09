# Phase 6 — Dynamic A* Routing (S1)

**Status:** ✅ Verified live on 29 May 2026.

## What changed

The AGV brain now computes its bay→input and output→bay routes via 4-neighbour
A* over the warehouse grid when smart mode is active. The A* respects:

- **Manual blockers** (operator-set via `POST /grid/obstacles {"cell":[c,r]}`).
- **Other AGVs' current cells** (soft obstacles — pulled from
  `/grid.runtime.occupancy`).

Falls back to the original `_straight_path` if no A* path exists OR if smart
mode is off (US 32 path preserved verbatim).

## What I implemented

### `app/models/grid_state.py`
- New blocker set `_blockers: set[(int, int)]` guarded by the existing `_lock`.
- Public helpers: `add_blocker`, `remove_blocker`, `clear_blockers`,
  `is_blocked`, `blocked_cells`.
- `snapshot()` now includes `blockers` so the AGV sees them via the existing
  `/grid` endpoint without a new route.

### `app/routes/agv_routes.py`
New route `GET / POST / DELETE /grid/obstacles`:

```
GET    /grid/obstacles               -> {"blockers": [[c,r], ...]}
POST   /grid/obstacles  {"cell":...} -> add a blocker
POST   /grid/obstacles  {"clear":true} -> wipe all
DELETE /grid/obstacles  {"cell":...} -> remove one
```

Used by tests and (later) by an operator UI.

### `scripts/agv_brain.py`
- New `_astar_path(start, end, cols, rows, blocked, occupied_other)` —
  4-neighbour A*, Manhattan heuristic, treats `blocked` cells as hard
  obstacles (except when the goal itself is blocked — then we still try to
  reach it), other-AGV cells as soft obstacles.
- New `_smart_path(start, end, grid_data)` — calls A*; falls back to
  `_straight_path` on no-path or any exception.
- New `_mode_is_smart()` helper.
- `_fetch_route_ctx()` now wraps each `_straight_path(...)` in
  `_smart_path(...)` when smart mode is on, plus replaces `_bay_return_path`
  with a `_smart_path` call so the return route also routes around obstacles.

### `docker/nginx.conf`
Already extended in Phase 5 to proxy `/grid` paths — `/grid/obstacles` is
covered by the existing `agv|grid|coord|bus` regex group.

## Features

- **Real path planning.** Routes change based on what's on the grid right
  now. Drop a blocker on a cell and the AGV detours.
- **Other-AGV awareness.** A second AGV occupying a cell is treated as a
  soft obstacle. A path that would walk through it is rerouted at the time
  the route is fetched — `_fetch_route_ctx` runs once at task start, so
  this is a "plan around current state" not "replan every step".
- **Goal cell is always reachable.** Even if the goal is on a blocker or
  another AGV's cell, the A* still returns a path ending on it (the dock
  cell, the bay) — otherwise routes would never finish.
- **Straight-line fallback.** Unsolvable A* (fully walled-in goal) →
  `_straight_path` returns a rectilinear path. The system never deadlocks
  on a routing failure.
- **US 32 path verbatim.** Under `decentralized` (or `centralized`),
  `_smart_path` is bypassed entirely; the original `_straight_path` and
  `_bay_return_path` calls return identical output to pre-US 34.

## Capabilities

| Field | Source | Used by |
|---|---|---|
| `grid.cols`, `grid.rows` | `pipeline.yaml:warehouse.grid` via `/grid` | A* search bounds |
| `runtime.blockers` | `grid_state._blockers` via `/grid` | A* hard-skip cells |
| `runtime.occupancy` | `grid_state._occupancy` via `/grid` | A* soft-skip cells (other AGVs only) |
| `pipeline.yaml:warehouse.lane.cells` | already used by US 32 | The lane stays config-driven — A* doesn't override the corridor |

The lane corridor is deliberately *not* A*-planned: it's a shared resource
with a `LaneLock`, and US 32's contract for the corridor is "everyone walks
the same cell sequence under exclusive ownership." Smart mode honours that.
What CHANGES under smart mode is the routes outside the lane (bay→input,
output→bay) and the lane fallback case (when no lane config exists).

## Live test results (29 May 2026 evening)

### Test 1 — clean route (no blockers, smart mode)

```python
COORDINATION_MODE = "smart"
route_to_input: [[10, 3], [9, 3], [8, 3], [7, 3], [6, 3], [5, 3], [4, 3],
                 [3, 3], [2, 3], [2, 2], [2, 1]]
```

11 cells — bay [10,3] cols-first to [2,3], then rows to dock [2,1]. Same as
the pre-US 34 straight line because A* on an empty grid returns the cheapest
path, which is identical.

### Test 2 — blocker placed on the path

```
POST /grid/obstacles {"cell":[5,3]}

COORDINATION_MODE = "smart"
route_to_input: [[10, 3], [9, 3], [8, 3], [7, 3], [6, 3], [6, 2], [5, 2],
                 [4, 2], [3, 2], [2, 2], [2, 1]]
```

The A* detoured: at [6,3] (the cell before the blocked [5,3]) it stepped
up to [6,2], walked across row 2, then dropped into the dock at [2,1].
Same length (11 cells) — A* found a minimal-cost detour.

### Test 3 — US 32 fallback (`COORDINATION_MODE=decentralized`)

Same blocker `[5,3]` still set on the backend:

```python
COORDINATION_MODE = "decentralized"   # US 32 mode
route_to_input: [[10, 3], [9, 3], [8, 3], [7, 3], [6, 3], [5, 3], [4, 3],
                 [3, 3], [2, 3], [2, 2], [2, 1]]
```

A* bypassed entirely. The route walks through [5,3] just as it did pre-US 34.
**US 32 fallback intact.**

## Limitations

- **One-shot planning.** A* runs once at task start (`_fetch_route_ctx`).
  Mid-route blocker changes are not re-planned. A future US could add
  per-step re-evaluation, but for the current 3-AGV demo this is acceptable
  (blockers are demo-time fixtures, not dynamic obstacles).
- **No cell reservations between AGVs.** Two AGVs can plan paths that cross
  the same cell at the same time. The existing `LaneLock` + station locks
  prevent dock collisions; cell-by-cell reservation is a follow-up.
- **Heuristic is Manhattan, not Euclidean.** Diagonal movement is not
  modelled because the warehouse grid is 4-neighbour. Correct.
- **Lane corridor not A*-planned.** Lane is a config-fixed sequence under
  `LaneLock` ownership; smart mode preserves that. See "Capabilities" note above.

## US 32 fallback verified

Test 3 above. With `COORDINATION_MODE=decentralized`, the path returned is
byte-for-byte identical to pre-US 34. The smart-mode A* and obstacle handling
are *purely additive* — the legacy code paths are untouched and unreachable
under non-smart modes.

## Next

Final consolidation:
- `docs/coordination-us34-evidence.md` — live transcripts proving each phase
  against the Taiga acceptance criteria.
- `docs/Work flow(12 June 2026).md` — workflow doc covering the smart path.
- Walk the US 34 Taiga acceptance checklist; tick each met item with its
  evidence; clearly list the deferred frontend items.
