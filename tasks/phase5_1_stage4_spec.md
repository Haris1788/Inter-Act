# Phase 5.1 Stage 4 Committed Sub-Spec — Dynamic Spatial Overlays

Replace deleted static grid with runtime-driven overlays. Live grid
(active cells only), reservation visualization, path preview, AGV trails
(already Phase 5).

## Decisions (approved)

- Reservation viz source: 1 Hz poll of new `GET /reservations`.
- `agv_path` SocketIO event: observational, `{actor_id, task_id,
  path_cells, epoch}` after `a_star` returns + before traversal.
- DynamicGridLayer: subtle dots ONLY at active cells (occupied +
  reserved + recent path). No always-on grid.

## Backend additions

### 1. `app/routes/replay_routes.py` — `GET /reservations`
Pure read. Snapshot of all reservations across all tasks/actors.
```python
@replay_bp.route("/reservations", methods=["GET"])
def reservations():
    """Snapshot of all current cell reservations.

    Returns {reservations: [{task_id, actor_id, epoch, cells: [[col,row],
    ...]}], ts}. Pure read, observational, no mutation.
    """
    from app.spatial.reservation import get_store
    store = get_store()
    # SCAN resv:task:*:actor:*:epoch:*:cell:*:* and group by (task, actor, epoch).
    snapshot = store.snapshot_all()  # new method added if needed
    return jsonify({"reservations": snapshot, "ts": time.time()})
```

### 2. `app/spatial/reservation.py` — `snapshot_all()` method
Pure read. SCAN all `resv:*` keys, group by `(task_id, actor_id, epoch)`,
return list of `{task_id, actor_id, epoch, cells: [[col, row], ...]}`.
Works in both backends (in-memory + Redis).

### 3. `app/agents/agv_agent.py` — emit `agv_path`
In `_traverse_to_with_path` right after the successful `a_star` +
`reserve` (before traversal begins):
```python
try:
    from app import socketio
    socketio.emit("agv_path", {
        "actor_id": agv_id,
        "task_id": task_id,
        "epoch": epoch,
        "path_cells": [[c[0], c[1]] for c in path],
        "ts": time.time(),
    })
except Exception:
    pass  # best-effort, no consensus impact
```
Observational only. Does not block traversal.

## Frontend additions

### 4. `frontend/src/canvas/overlay/ReservedCellsLayer.tsx` (new)
Polls `GET /reservations` every 1 s. For each reservation, renders the
cells as filled `<Rect>` with the actor's color at 25% alpha (look up
color from `topologySlice.actors[actor_id].color`).

Cell render: `x = col * cell_px`, `y = row * cell_px`, `width = cell_px`,
`height = cell_px` where `cell_px = 40` (matches backend).

Self-owned reservations differentiated subtly (selected actor's own
reservations get a brighter outline).

### 5. `frontend/src/canvas/overlay/PathPreviewLayer.tsx` (new)
Subscribes to `agv_path` SocketIO event via `socketDriver.ts`. Stores
per-actor `{path_cells, epoch, ts}` in a new runtime slice field
`agvPaths: Record<actorId, {cells, epoch, ts}>` (or a focused new slice).

On render: for each active path, render a faint dashed polyline
connecting cell centers (`(col + 0.5) * cell_px`). Color = actor.color
at 30% alpha. Dash `[4, 4]`. Stroke width 2.

When the corresponding actor's `agv_position` updates and current cell
is past a path cell, that cell is "consumed" — render the remaining
path from current position to goal. Simple impl: render the full path
always until a new `agv_path` event arrives or a `LEG_COMPLETED` from
that actor fires (clear the path then).

### 6. `frontend/src/canvas/overlay/DynamicGridLayer.tsx` (new)
Subtle dots ONLY at cells with current activity:
- Occupied cells (from `agv_state.cell` in `actorRuntime`).
- Reserved cells (from `/reservations` poll — share state with
  ReservedCellsLayer via a common slice if cheap, else duplicate poll).
- Recent path cells (from `agv_path` events in the last ~30 s).

For each active cell: render a tiny dot (radius 2 px) at the cell
center, color `rgba(150, 150, 150, 0.3)`.

NO always-on full-grid render. The environment emerges from runtime.

### 7. `socketDriver.ts` — `agv_path` handler
Add handler. Dispatch to a new slice action `setAgvPath(actor_id,
{path_cells, epoch, ts})`. Idempotent — newer epoch supersedes.

### 8. `runtimeSlice.ts` or new slice — `agvPaths` field
```ts
type AgvPath = { cells: [number, number][]; epoch: number; ts: number };
agvPaths: Record<string, AgvPath>;
setAgvPath: (actorId: string, path: AgvPath) => void;
clearAgvPath: (actorId: string) => void;
```

### 9. `KonvaOverlay.tsx` — mount the 3 new layers
Z-order (low to high):
- DynamicGridLayer (background dots)
- ReservedCellsLayer (active-cell fills)
- PathPreviewLayer (dashed routes ahead)
- ArmRenderer (Phase 5)
- AgvTrailLayer (Phase 5)
- AgvTokenLayer (Phase 5 + per-kind silhouettes)

## Hard invariants

- All 3 new overlays read ONLY runtime state. NO hardcoded geometry.
- `agv_path` SocketIO event is observational; never consulted for
  consensus.
- `/reservations` is a pure read; no mutation.
- TypeScript strict; backend pytest unchanged.
- 614-pass baseline keeps passing.

## Verify

- `python -m pytest tests/ -q --ignore=tests/distributed/test_chaos.py` →
  baseline. After: ≥ baseline + new tests / 5 pre-existing. Zero
  regressions.
- `cd "D:\Inter Act\frontend" && npm run typecheck && npm run build` —
  clean.

## Opus review gate

- All overlays render from live SocketIO + `/bus/replay` +
  `/reservations`. NO hardcoded geometry.
- `agv_path` doesn't influence consensus.
- `/reservations` SCAN cost bounded (snapshot_all uses per-prefix scan).
- DynamicGridLayer renders empty when nothing is active (correct
  "empty world = blank canvas" behaviour).
