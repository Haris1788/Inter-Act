# Phase 5.1 Stage 1 Committed Sub-Spec — Delete Legacy Renderer

Foundation. Frontend-only. Single track. Deletes the Haris warehouse
patch, blue lane, ArmLayer, WarehouseGridLayer, and purges camera-level
remnants (fitView / minimap / initial viewport all hardcoded warehouse
dims). Guts `ActorNode` to an invisible RF shell. Existing Phase 5 Konva
renderers (ArmRenderer / AgvTokenLayer / AgvTrailLayer) become the
canvas.

## Decisions (approved)

- ReactFlow stays as invisible pan/zoom + drag-detect.
- `ZoneNode` (feed-I / sink-O markers) — DELETE.
- MiniMap — DROP entirely (canvas IS the simulation per spec).
- Viewport / fitView bounds — derive from `TopologyActor[]` only.

## Required behaviour

- After Stage 1, the canvas shows ONLY: ArmRenderer (Phase 5),
  AgvTokenLayer (Phase 5), AgvTrailLayer (Phase 5). Nothing else
  visually.
- Empty topology → blank canvas centered at sensible default (NOT
  centered on a ghost 14×10 grid).
- Placing one actor → fitView frames on that actor.
- RF nodes remain in the React tree (so drag/pan/zoom/snap still work)
  but render NOTHING visible.
- All `/config` warehouse-dim reads in the canvas tree → removed.
- `npm run typecheck && npm run build` clean.

## Files

### 1. DELETE entirely
- `frontend/src/canvas/overlay/WarehouseGridLayer.tsx`
- `frontend/src/canvas/overlay/ArmLayer.tsx` (Phase 5 `ArmRenderer`
  supersedes)
- `frontend/src/canvas/flow/nodes/ZoneNode.tsx` (and any `zone`-type
  registration in the RF nodeTypes map; verify zones aren't referenced
  by tests in tests/ — if so, prune)

### 2. `frontend/src/canvas/flow/nodes/ActorNode.tsx` — gut to invisible

Keep the React component so RF's nodeTypes map still resolves, but the
render returns a transparent placeholder sized to match the actor's
hit-area for drag (e.g. `<div style={{ width: 1, height: 1, opacity: 0,
pointerEvents: 'none' }} />` — or a sized invisible div that matches the
actor's spatial footprint so RF position math stays consistent with
authored bounds). All selection rings, label chips, status pills, badges
removed. NO visible pixel.

### 3. `frontend/src/canvas/overlay/KonvaOverlay.tsx` — purge mounts

Remove `WarehouseGridLayer` + `ArmLayer` imports + mounts. The Konva
layer stack becomes:
- `ArmRenderer` (Phase 5)
- `AgvTrailLayer` (Phase 5)
- `AgvTokenLayer` (Phase 5)

In that z-order: arms underneath, trails above, AGV bodies on top. (S4
inserts the new dynamic overlays between trails and tokens.)

### 4. `frontend/src/canvas/CanvasViewport.tsx` — camera purge

- Remove the `<MiniMap>` component import + mount entirely.
- Remove any `<Background>` warehouse grid pattern that's tied to
  warehouse dims (the Phase 5 Stage 2C snap-grid `<Background
  variant="dots" gap={40}>` STAYS because it's toggleable + tied to
  the canvas-px grid, not warehouse).
- Initial viewport / `defaultViewport` / any `fitView()` call →
  replace bounds source.
- New helper `topologyBounds(actors: TopologyActor[]) ->
  {x, y, width, height}`:
  ```ts
  export function topologyBounds(
    actors: TopologyActor[],
    pad = 160,
  ): { x: number; y: number; width: number; height: number } {
    if (actors.length === 0) {
      return { x: 0, y: 0, width: 800, height: 600 };
    }
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const a of actors) {
      const r = a.reach_px ?? 40;
      minX = Math.min(minX, a.position.x - r);
      minY = Math.min(minY, a.position.y - r);
      maxX = Math.max(maxX, a.position.x + r);
      maxY = Math.max(maxY, a.position.y + r);
    }
    return {
      x: minX - pad,
      y: minY - pad,
      width: (maxX - minX) + 2 * pad,
      height: (maxY - minY) + 2 * pad,
    };
  }
  ```
- Live at e.g. `frontend/src/canvas/topologyBounds.ts` so tests + other
  callers can import.
- Replace any `useFitViewport()` warehouse-aware variant with one that
  fits to `topologyBounds(useTopologyActors())`.

### 5. `frontend/src/canvas/` — purge `/config` warehouse fetches

Grep `frontend/src/` for `/config` fetches that read the `warehouse` /
`grid` / `lane` / `stations` blocks for CAMERA or RENDER purposes.
Remove every such reader from the canvas tree.

`/config` reads that survive: Inspector's "live system" capability
section, sidebar metadata, etc. — those are informational sidebar
content and stay.

### 6. RF nodeTypes registration

Wherever `nodeTypes` is built (probably in `flowConfig.ts` or
`CanvasViewport.tsx`):
- Remove `zone: ZoneNode`.
- Keep `actor: ActorNode` (but ActorNode now invisible).
- Drop any other workflow-editor-style node types.

### 7. Tests

- `frontend/` tests: if vitest exists, add a unit test for
  `topologyBounds`:
  - empty actors → `(0, 0, 800, 600)`.
  - one actor at (100, 100) with reach 50 → bounds covering 100±50
    padded.
  - three actors at varied positions → union bounded + padded.
- If RF/Konva snapshot tests exist (unlikely), update or remove. Don't
  add new snapshot tests — the visual is the new spec, not a snapshot.

## Hard invariants

- No visual pixel from WarehouseGridLayer / ArmLayer / ActorNode after
  this stage.
- Drag + pan + zoom + snap (RF native) all keep working — invisible
  ActorNode shells still receive RF events.
- `topologySlice` is the single source for actor positions used by both
  RF (drag math) and Konva (rendering).
- Camera bounds NEVER read from `/config` warehouse dims after Stage 1.
- TypeScript strict; `npm run typecheck && npm run build` clean.
- Phase 5's 614-pass pytest baseline UNCHANGED (this is frontend-only).

## Verify

- `cd "D:\Inter Act\frontend" && npm run typecheck` clean.
- `npm run build` clean.
- Manual via Playwright (loaded earlier this session, fine for Stage 1
  visual verification — Phase 5.1's Stage 9-equivalent E2E uses
  agent-browser later if needed):
  - Empty topology → no blue lane, no station boxes, no actor cards.
  - Drag an arm onto canvas → invisible RF node tracks position; Konva
    renders the articulated arm at that position.
  - Drag an AGV → invisible RF node; Konva renders vehicle silhouette.
  - Click an empty canvas area → no actor card pops up.

## Opus review gate

- No hidden re-mount of deleted layers (grep confirms 0 imports of
  `WarehouseGridLayer` / `ArmLayer` / `ZoneNode` anywhere).
- `ActorNode` returns exactly one transparent element; no conditional
  visible branch survives.
- `topologyBounds` derives ONLY from `TopologyActor[]`; zero `/config`
  references inside the camera/viewport path.
- MiniMap mount removed everywhere.
- Selection + drag still work through invisible RF nodes (proven by
  manual interaction).
- Phase 5 Konva renderers (ArmRenderer / AgvTokenLayer / AgvTrailLayer)
  are the only visible layers in the Konva stack.

## What this is NOT

- NOT adding per-kind AGV silhouettes (S3).
- NOT adding dynamic spatial overlays (reservations / paths) (S4).
- NOT polish (S5).
- ONLY the structural deletion + camera purge that enables every later
  stage to assume "canvas = Konva, viewport = topology".
