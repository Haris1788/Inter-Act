# Phase 5.1 Stage 2 Committed Sub-Spec — Konva-as-Canonical-Actor

After Stage 1 deleted the legacy visuals + gutted `ActorNode`, Phase 5's
Konva renderers (ArmRenderer / AgvTokenLayer / AgvTrailLayer) ARE the
canvas. Stage 2 makes that official: position single-source from
`topologySlice`, selection routes through Konva click → `editorSlice`
selectionId, RF's own selection becomes a mirror.

## Decisions (approved)

- Selection source-of-truth: Konva click dispatches to `editorSlice`
  (or whatever holds `selectedActorId`); RF mirrors.
- Position single-source: `topologySlice.actors[id].(x, y)`. RF reads
  from it for drag math; Konva reads from it for render. NEVER read the
  other system's view as authoritative.

## Required behaviour

- Click a Konva entity (arm pivot, AGV body) → editor slice's
  `selectedActorId` updates → Inspector reflects the selection.
- Click empty canvas area → selection clears.
- Drag an actor (RF-handled via invisible `ActorNode`) → slice updates
  position → Konva re-renders at the new position in real time.
- No phantom selection state. RF and Konva agree on the selected actor.
- Inspector edits (reach radius, capacity, speed) live-update the
  Konva visual.

## Files

### 1. `frontend/src/canvas/overlay/ArmRenderer.tsx` (verify + add hit area)
- Confirm position read is `topologySlice` (not RF). Phase 5 already did
  this — just verify.
- Add an invisible/subtle hit area: a circle around the pivot at radius
  `max(reach_px, 30)` listening for clicks. On click → call
  `editorSlice.selectActor(actor.id)`.
- Selected state: when `actor.id === editorSlice.selectedActorId`, render
  the reach radius at 30% alpha (already in Phase 5 — confirm).

### 2. `frontend/src/canvas/overlay/AgvTokenLayer.tsx` (verify + add hit area)
- Same: position from slice, runtime override from `agv_position`
  SocketIO (Phase 5).
- Add click handler on the vehicle body → `editorSlice.selectActor`.
- Selected state: subtle outline + brighter status dot OR existing
  selected-highlight pattern if any.

### 3. `frontend/src/canvas/overlay/KonvaOverlay.tsx` — empty-canvas click
- Add a transparent Konva `<Rect>` filling the whole stage at the
  BOTTOM of the layer stack listening for clicks. On click →
  `editorSlice.selectActor(null)`. Stops propagation? No — only fires if
  no entity above intercepted.

### 4. `frontend/src/canvas/CanvasViewport.tsx` — RF onSelectionChange
- If RF emits a `onSelectionChange` based on its own drag, mirror it to
  `editorSlice.selectActor(node.id)`. The intent: dragging an actor
  selects it. If RF doesn't auto-select on drag, no change needed.
- Verify no RF code AUTHORS the selection (e.g. clicking an invisible
  shell shouldn't trigger RF selection that conflicts with Konva's
  click). Disable RF's built-in node-click selection via the
  `nodesFocusable={false}` / `selectionOnDrag={false}` props if needed.

### 5. Test
If vitest is configured: small selector test asserting clicking an
actor's hit-area triggers `editorSlice.selectActor`. Otherwise verify
manually.

## Hard invariants

- ONE position source: `topologySlice.actors`. RF and Konva both read
  from it; neither writes the OTHER side's representation.
- ONE selection source: `editorSlice.selectedActorId`. Konva click
  writes; RF mirrors / doesn't compete.
- No phantom selection state.
- Empty canvas click clears selection.
- TypeScript strict; `npm run typecheck && npm run build` clean.

## Verify

- `cd "D:\Inter Act\frontend" && npm run typecheck` clean.
- `npm run build` clean.
- Manual:
  - Drop two actors → click each → Inspector shows the correct one.
  - Click empty area → Inspector empties.
  - Drag actor → it moves, selection follows (or doesn't, but
    consistent).
  - Reach slider in Inspector → Konva reach circle updates instantly.

## Opus review gate

- Position double-truth absent: `topologySlice` is the only writeable
  source. RF position writes flow through `moveActor` (or whatever
  Phase 3 wired).
- Selection double-truth absent: editor slice is sole truth; RF
  selection (if present) is read-only mirror.
- Click on empty canvas → selection cleared.
- Drag still works through invisible ActorNode shell.
