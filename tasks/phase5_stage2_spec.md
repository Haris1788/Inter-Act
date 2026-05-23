# Phase 5 Stage 2 Committed Sub-Spec — Drag-and-Drop Orchestration Canvas

Frontend-only. Promote authored topology from decorative (Phase 3) to
canonical (Phase 5). Backend untouched in Stage 2 — wiring lands in Stage 3.

## Tracks

A (blocking, single track) → B ‖ C ‖ D (concurrent after A's schema lands).

### Track A — Zustand topology slice schema + drag-drop wiring  [single track]

**Files:** `frontend/src/store/topologySlice.ts` (or current slice file —
find it; ReactFlow uses Zustand), `frontend/src/canvas/CanvasViewport.tsx`,
`frontend/src/canvas/LibChip.tsx`, related.

Design:
- Existing Phase 3 drag-from-sidebar already works for actor cards but the
  position is decorative. Stage 2A extends the per-actor entry to carry the
  full spatial spec:
  ```ts
  type AuthoredActor = {
    id: string;          // "arm-1", "agv-1" auto-numbered per kind
    kind: "arm" | "agv";
    x: number;           // canvas px (RF flow coords)
    y: number;
    reach_px: number;    // arms only meaningful, default 120
    capacity: number;    // default 1
    speed: number;       // default 1.0
    label?: string;
    color?: string;
  };
  ```
- Drag from sidebar → drop on canvas: create an `AuthoredActor` at the drop
  flow-coord. Auto-assign next id (`arm-N` / `agv-N`).
- Move via ReactFlow drag updates `x, y` on the slice entry.
- Delete via Del key or Inspector button.
- localStorage persistence schema extended; migration: if old schema lacks
  `reach_px`/`capacity`/`speed`, default them in.
- No deploy payload yet (Track D); no Inspector field yet (Track B); no snap
  yet (Track C).

### Track B — Inspector spatial fields  [parallel]

**Files:** `frontend/src/chrome/Inspector/...`.

Design:
- Show `(x, y)` position (read-only — drag on canvas to change).
- Reach radius slider (40–400 px) — arms only; AGVs hide.
- Capacity stepper (1–4).
- Speed stepper (0.5–3.0 step 0.5).
- Changes write back through the topology slice.

### Track C — Grid snapping + visual snap indicator  [parallel]

**Files:** `frontend/src/canvas/CanvasViewport.tsx` (snap math),
optional grid overlay.

Design:
- Toggle in canvas toolbar: "Snap to grid" (default ON, persisted).
- `cell_px = 40` (matches Stage 1 backend default).
- On drag-end, if snap ON, round `x, y` to nearest multiple of `cell_px`.
- Hold Shift while dragging = snap OFF for that drag.
- Optional faint grid overlay rendered behind the canvas when snap ON.

### Track D — Deploy payload serializer  [parallel]

**Files:** `frontend/src/runtime/deploy.ts` (or whatever Phase 3 used).

Design:
- Existing `/deploy/apply` already takes `{arms, assets, ...}`. Stage 2D adds
  a `topology:` field to the payload:
  ```ts
  topology: {
    canvas: { width, height, cell_px },
    actors: AuthoredActor[],     // straight from the topology slice
  }
  ```
- Backend Stage 3 reads this. Stage 2 only serializes the field — backend
  ignores it for now (warehouse: block still wins until Stage 3).
- No new endpoint; reuses `/deploy/apply` POST.

## Hard invariants

- Pure frontend. No backend changes.
- Existing Phase 3 drag/drop behaviour preserved — Stage 2 EXTENDS, doesn't
  rewrite. If a Phase 3 user drags an actor, the same drag still works; only
  the persisted shape is richer.
- localStorage migration MUST be additive — old saved workspaces load
  cleanly with defaults filled in.
- TypeScript strict — `npm run typecheck` passes.
- `npm run build` passes.

## Verify

- `cd "D:\Inter Act\frontend" && npm run typecheck` clean.
- `npm run build` clean.
- A focused frontend test (if vitest is configured) for the slice migration:
  load an old-shape workspace, assert defaults fill in.
- Manual smoke (Playwright OR you): drag arm + AGV; reload; positions
  persist; Inspector shows reach slider; snap toggle works; deploy payload
  includes `topology` field (inspect via DevTools Network).

## Opus review gate

- Track A: slice schema is additive — no breaking field rename.
- Track D: deploy payload schema is documented + backward-compatible (no
  field removed).
- No mock/fake data introduced — fields come from real user drag.
