# Phase 14 — Operational layout coherence + interaction correctness

Frontend-only (React app `frontend/src`). No backend, no AI, no arch rewrite, preserve RF/Konva + 4-slice.

## Root causes

1. **Inspector header desync**: Inspector uses 3 split subscriptions (`useSelectedActorId`, `useActor`, `useActorRuntime`). Spec mandates ONE authoritative selector → header/body/runtime/actions atomic. Also header icon is a hardcoded AGV glyph (kind-blind).
2. **Collapse partial**: `LogsPanel` only hides `TabBody`; header stays but dock keeps its grid row. Collapse must shrink the whole dock + give canvas the space, animated, restore height.
3. **Minimap blocks**: Phase-13 nodeColor returns solid `#18181B`/`#5F8F58` fills → black/green rectangles. Need subtle muted stroke style.
4. **No hierarchy**: Fleet competes as a tab. Split bottom into left 65% stream + right 35% permanent Fleet sidebar.
5. **Fleet tab redundant**: remove `fleet` TabId; left tabs = Coordination/Message bus/Validation/Events.
6. **Minimap float**: anchor bottom-right, above dock, never clip/overlap inspector, subtle elevation.
7. **Density**: tighter spacing, calmer separators, scroll containment, aligned headers.

## Tasks

- [x] `selectors.ts`: `useSelectedActorBundle()` (one `useStoreEq` subscription) → Inspector single source; kind-aware header icon
- [x] `editorSlice`+`types`: `logsCollapsed`, `logsHeight`, `toggleLogsCollapsed`, `setLogsHeight`; App grid `1fr auto`; animated dock height
- [x] `LogsPanel`: 65/35 split, remove fleet tab, store-driven collapse, permanent Fleet right sidebar
- [x] `CanvasViewport` MiniMap: muted stroke palette, anchored, not clipped
- [x] Density pass across panes
- [x] Verify (typecheck+build), docs, local commit (no push)

## Phase 15 (3 remaining regressions)

- [x] Issue 1 Label desync — already fixed `cf93c1b` (controlled, store-backed); verified, no change
- [x] Issue 3 edit icon — linked graph nodes + pencil, same style
- [x] Issue 2 — `deploy_bridge.apply_topology` + `POST /deploy/apply`; `deploy.ts` posts payload, ready only on `applied` + per-arm health (no optimism, honest no-fake)
- [x] 7 bridge tests; 142/142 pytest; typecheck+build clean
- [ ] Browser/Docker E2E — cannot run here (no live stack); listed in HANDOFF

## Review

All 7 issues closed, frontend-only, no arch change.

- **Header desync**: real fix = collapse 3 split subscriptions into one `useStoreEq` bundle (atomic, no tearing) per spec — also fixed a real bug where the header icon was a hardcoded AGV glyph on every actor.
- **Collapse**: had to lift collapse state to `editorSlice` because App owns the grid; dock animates its own height (`1fr auto`), slim 36px strip retained.
- **Minimap**: Phase-13 solid `#18181B`/`#5F8F58` fills → muted low-opacity warm-gray fill + visible stroke callbacks. RF MiniMap can't do true stroke-only; this reads as a subtle thumbnail.
- **Split/Fleet**: `LogsPanel` now left-65 stream / right-35 permanent Fleet; `fleet` TabId removed everywhere; tabs only drive the left.
- **Density**: aligned the Fleet header to the tab strip (both h-9), enforced `min-h-0 flex-1 overflow-y-auto` containment, dropped LogStream's redundant border.

Verification: typecheck + build clean. **Browser smoke not run** (no live stack here) — must verify reselect/collapse/minimap/split visually before declaring interaction proven (see HANDOFF.md). Local commit only, no push.

---

# Phase 6 — Deploy Runtime Parity

Authored actors (arms, AGVs) must become first-class runtime actors after Deploy.

## Implementation

- [x] Step 1: `deploy.ts` — add `size:{w,h}` to `DeployActor` + `buildDeployPayload`
- [x] Step 2: `deploy_bridge.py` — `_px_to_cell`, `_build_warehouse_section`, `_validate_authored_warehouse`; extend `build_pipeline_yaml` to return `(doc, warnings)`; preserve seeded warehouse; merge authored stations/bays
- [x] Step 3: `deploy_bridge.py` — call `agv_state.initialize_from_config()` in step 3 after reload
- [x] Step 4: `container_manager.py` — add `"vehicle"` kind → `Dockerfile.agv`; fix env vars (`AGV_ID`, `COORDINATION_MODE`, capabilities); fix port 8100
- [x] Step 5: `dispatch_routes.py` — replace hardcoded `_AGV_CONTAINER_URLS` with dynamic `_agv_url()` from agv_state
- [x] Step 6: `useFlowNodes.ts`, `KonvaOverlay.tsx` (StaticLayer+ArmLayer), `WarehouseGridLayer.tsx`, `AgvTokenLayer.tsx` — add `deployedAt` dep; exclude seeded AGV IDs from authored bay render
- [x] Step 7: Validation warnings in `build_pipeline_yaml` for out-of-bounds cells, duplicate cells, bay/station overlaps

## Regression audit

Zero new test failures. All 7 failures are pre-existing baseline:
- `test_agv_brain` (2): pre-existing mock issue
- `test_arm_execution` (1): pre-existing arm_id=3 not in pipeline.yaml
- `test_warehouse_config` (4): pre-existing config drift (documented in CLAUDE.md gotcha #10)

## Evidence

### Coordinate math proof
- Authored arm-1 at RF (95,135) + size(210,150) → center(200,210) → cell[1,1] → dock[2,1] ← exact match seeded config
- Authored arm-2 at RF (1375,135) → center(1480,210) → cell[12,1] → dock[11,1] ← exact match seeded config
- Round-trip: cell_to_px([1,1]) = {x:210,y:210}; _px_to_cell(210,210) = [1,1] ✓

### Seeded topology preservation
- agv-1/2/3 bays preserved: agv-1.bay=[10,3] matches original ✓
- Lane preserved ✓
- Grid/origin never overridden ✓

### Browser/Docker E2E — must verify on live stack:
1. Deploy authored topology
2. Confirm /config contains new station/bay entries
3. Confirm runtime actor/container exists
4. Confirm actor renders through runtime path
5. Run AI Plan → Dispatch chain
6. Authored actor participation
7. Zero task_failed

---

# Phase 7 — Canvas Source-of-Truth migration

Spec: `tasks/phase7_staged_spec.md`. Branch `phase7-canvas-sot` off `dev-haris`.
Baseline: `tasks/phase7_app_baseline.diff` (514 lines) = frozen `app/` state.
Isolation gate each stage: `git diff dev-haris -- app/` must equal baseline.

## Stage A — Zone actor unification (DONE, uncommitted)

Files changed (frontend only): `store/types.ts`, `lib/actorNormalizer.ts`, `runtime/deploy.ts`.

- [x] `ActorKind += "zone"` (dormant — no path emits zone actors yet)
- [x] `TopologyActor.subtype?` added; `normalizeActor` zone-aware (type/size/caps/brain) + subtype default
- [x] `DeployActor.kind` widened to accept "zone" (inert type propagation; payload logic deferred to Stage E)
- [x] typecheck + build green; isolation gate PASS (app/ == baseline)

### Scope refinement vs spec (documented)
Spec listed `nodeFactory.ts` + `topologySchema.ts` in Stage A. **Deferred to Stage B** because:
- Folding zone into `createActorFromPalette` makes `onDrop` (tries it first) create zone-actors immediately → un-migrated `useFlowNodes`/`ActorNode` render an ugly zone CARD → behavior change, violating Stage A "zero behavior change".
- Dormant `nextZoneId`/`ZONE_SIZE` in nodeFactory = unused symbols → `noUnusedLocals:true` build break.
- Legacy `zones[]→actor` shim in topologySchema would surface zone cards on load (same reason).
Both land in Stage B alongside the onDrop + useFlowNodes + ActorNode flip (their natural home). Stage A is now pure dormant type/normalizer extension.

### E-diff (E:/Inter Act reference rule)
- **Adopted from E:** ActorKind "zone", `subtype` field, normalizer zone branch (size 100×100, caps [], brain "—").
- **NOT adopted:** E's `reach_px`/`capacity`/`speed` (Phase 5 fields D never had; out of Phase 7 scope; adding would touch Inspector/deploy/runtime).
- **Kept (D diverges from E):** `authored` field — E dropped it (all invisible shells); D still card-gates via `konvaOnly` until the Stage B card→shell flip, so `authored` stays until Stage F.
- **Kept (out of scope):** `pool` / `TopologyPool` — E removed; not part of zone unification.

## Stage B — next (awaiting approval)
nodeFactory zone-fold + onDrop unify + useFlowNodes all-actorNode + ActorNode invisible shell + topologySchema zone shim.

### Stage B baseline audit (captured after Stage A commit 249a478)

Branch `phase7-canvas-sot`. These are the legacy zone touchpoints Stage B must convert.

**`type: "zoneNode"`** (1 — the legacy RF zone node):
- `canvas/flow/useFlowNodes.ts:70` ← Stage B replaces with unified actorNode.

**`createZoneFromPalette`** (2 call/def sites + comments):
- `canvas/CanvasViewport.tsx:29` import; `:149` onDrop call ← Stage B: drop, route via createActorFromPalette.
- `runtime/nodeFactory.ts:121` comment; `:153` definition ← Stage B: fold into createActorFromPalette (delete in Stage F).

**`TopologyZone`** (legacy type usage — converts across B/C/F):
- type def: `store/types.ts:53`; slice field `:76`.
- `canvas/CanvasViewport.tsx:146` (comment); `canvas/flow/nodes/ZoneNode.tsx:10,12` (Stage F delete).
- `lib/validation.ts:3,15`; `lib/topologySchema.ts:8,20,27,47,77`.
- `runtime/configNormalizer.ts:7,42,60`; `runtime/deploy.ts:6,37`; `runtime/mockData.ts:8,89`; `runtime/presets.ts:7,16,68`; `runtime/nodeFactory.ts:1,4,9,15,148,157`.

**`zones:` in store** (2 — the slice to retire in Stage F):
- `store/types.ts:76` (slice type); `store/topologySlice.ts:14` (initial state `zones: {}`).

Note: zone schema (ActorKind"zone"/subtype/normalizer) already in place from Stage A — these legacy sites still use the OLD separate-zones model and are the Stage B/C/F conversion targets. None touched yet.

**STOP — do not begin Stage B implementation until approved.**

---

## Stage B — IN PROGRESS (uncommitted). E-diff notes (documented before editing)

Path correction: user file list cited `canvas/nodes/ActorNode.tsx` + `canvas/nodes/index.ts`; D's real paths are `canvas/flow/nodes/ActorNode.tsx` + `canvas/flow/nodeTypes.ts`. Using D's real paths.

6 files (4 spec + 2 deferred from Stage A):

1. **nodeFactory.ts** — E folds zone into `createActorFromPalette` (ZONE_SIZE, nextZoneId, subtype). D returns null for zone + separate `createZoneFromPalette`.
   - ADOPT E fold. D PaletteDescriptor uses `role` (not E's `subtype`) → map `subtype: d.role` in zone branch (avoids touching `createZoneFromPalette`, which keeps using `role`).
   - Reuse D's existing `nextZoneId` (ids `zone-input-N`/`zone-output-N`) rather than E's `zone-source-N` — preserves D's id convention for existing workspaces.
   - KEEP `createZoneFromPalette` (now orphaned; exported so `noUnusedLocals` ignores it) → deleted Stage F. E has no such fn.

2. **CanvasViewport.tsx** — E `onDrop` = single path (createActorFromPalette→addActor→select). D has extra zone fallback (createZoneFromPalette→replaceTopology, lines 145-163).
   - ADOPT E single-path. Remove zone fallback + `createZoneFromPalette` import + `replaceTopology` (becomes unused in this file). handleNodeClick/ContextMenu/delete/duplicate already identical to E (act on selectedIds → zones inherit).
   - NOT adopting E's `snapEnabled` in handleNodesChange (snap out of Phase 7 scope).

3. **useFlowNodes.ts** — E = all actorNode invisible shells, no zone branch, no /config, no konvaOnly, no pool. D has all four.
   - ADOPT E model. Drop zones loop, `seededArmIds` /config fetch, konvaOnly, pool. Keep D's first-class width/height + nodeStyle (both D and E set these; RF wrapper uses them for hit area).

4. **ActorNode.tsx** — E = pure invisible 1×1 shell (opacity:0, pointerEvents:none), zero imports beyond memo/NodeProps/type. D = full card (glyph, runtime, bid, coherence, inline label-edit) + a konvaOnly sized-div-with-selection-ring branch.
   - ADOPT E pure shell wholesale. **DIVERGENCE/transient:** E's shell shows NO on-canvas selection ring; D's card/konvaOnly path did. Until Stage C/D add Konva selection visuals, selection feedback is Inspector-only. Documented, non-blocking. Inline label-edit → Inspector (already store-backed via updateActor).

5. **nodeTypes.ts** — E = `{ actorNode }` only. D = actorNode+zoneNode+poolLabelNode.
   - ADOPT E. Drop zoneNode/poolLabelNode entries + imports. ZoneNode.tsx/PoolLabelNode.tsx files left on disk (orphaned, no importers) → deleted Stage F.

6. **topologySchema.ts** — E keeps a separate zones slice in docToState (E itself mid-migration). D goal = single source (actors).
   - DIVERGE FROM E (toward cleaner end state): docToState folds doc.zones[] → kind:"zone" actors; returns `zones: {}` (single source = actors). serializeTopology derives `zones[]` from zone actors for doc back-compat + round-trip.
   - Safe: `deployable = list.length>0 && errors===0`; source/sink checks are `warn` not `error` → empty slice does NOT block deploy. **Transients (→ Stage F):** validation emits 2 non-blocking "no source/sink zone" warnings; contract.ts zone-owner payload resolution dormant (reads empty slice). Both resolved when Stage F migrates validation/contract to read zone actors.

## Stage C — store-driven canvas renderers (DONE, committing)

Files: NEW `canvas/overlay/ZoneMarkerLayer.tsx`; `canvas/overlay/KonvaOverlay.tsx` (+5: import + mount). Backend untouched (gate PASS).

### E-diff (E:/Inter Act reference rule)
- **ZoneMarkerLayer:** ported near-verbatim from E. Constants identical (radius 40; source green fill/stroke + "IN"; sink red + "OUT"); reads `useActors().filter(kind==="zone")`, `listening={false}`, locked to viewport tx, centred at position+size/2. Only diff = D import paths + header comment. ADOPTED.
- **ArmRenderer: NOT ported (documented divergence).** E's ArmRenderer is ~30KB and depends on `reach_px`/`capacity` (E Phase-5 fields D deliberately did NOT adopt in Stage A) plus tween/breathing/negotiation-pulse state. D ALREADY renders authored arms from actor positions via `ArmLayer.authoredStations` (store-driven, my earlier fix). Porting E's version would (a) double-render against ArmLayer, (b) force reach_px scope-creep into Stage A's schema, (c) add large risk — for zero outcome gain, since the required behavior ("authored arms render from actor positions only") is already met. Remaining /config coupling on arms = reach-scale (gridCp) + seeded-arm render + color; all are scaffold reads removed in Stage D.
- **Authored AGVs:** already render from actor positions (`AgvTokenLayer.authoredEntries`, pos = actor.position + size/2, target:null). No change needed; only /config coupling is COLOR (authoredAgvColor fallback) → Stage D.

### Verification evidence (agent-browser, mock mode, localhost:5180)
1. Drop Input Zone → GREEN dashed "IN" circle visible immediately (actor count 5→7). PASS
2. Select + arrow-nudge → green marker follows live (moveActor → ZoneMarkerLayer re-render). PASS — Inspector showed "Zone Input 1" / zone-input-1.
3. Delete → marker disappears (count 7→6, Inspector cleared). PASS
4. Refresh (reload) → Output zone restored from localStorage (6 actors persisted, red OUT circle). PASS
5. Duplicate (Ctrl+D) → 2nd red OUT marker at +36,+36 (count 6→7). PASS
6. Backend: `git diff dev-haris -- app/` == baseline. PASS
Drag target confirmed `react-flow__node-actorNode ... draggable` (zone is a first-class actor shell). Inspector shows zones with Actor ID `zone-input-1`/`zone-output-1`, Type Input/Output Zone.
Screenshots: /tmp/p7_zones.png, p7_zoomed.png (green IN), p7_nudged.png (moved), p7_deleted.png (gone), p7_refresh.png (restored), p7_dup.png (duplicated).

## Stage D — remove static scaffold + /config canvas geometry (DONE, committing)

Files: `canvas/overlay/KonvaOverlay.tsx` (StaticLayer + ArmLayer rewritten store-driven; removed WarehouseGridLayer import+mount; removed useDeployedAt); DELETE `canvas/overlay/WarehouseGridLayer.tsx`. Backend untouched (gate PASS).

### What was removed
- `WarehouseGridLayer.tsx` (whole file): grey lane corridor, IN/OUT station markers, seeded AGV bay squares — all /config-driven scaffold.
- `StaticLayer` /config fetch + seeded halos → now renders reach halos for ALL kind:"arm" actors from actor.position, scale = ARM_CELL_PX constant (120).
- `ArmLayer` /config fetch + seeded stations + authored/seeded split → now renders ALL kind:"arm" actors from actor.position; dock east (cx+ARM_CELL_PX); no warehouse.stations.

### E-diff
- E deleted StaticLayer/ArmLayer/WarehouseGridLayer entirely and rendered arms via ArmRenderer (reach_px-based). D KEEPS StaticLayer/ArmLayer as the arm renderer but makes them fully store-driven (no /config), using a fixed ARM_CELL_PX (D never adopted reach_px — Stage A). Same OUTCOME (arms from actor positions, no scaffold); different mechanism, justified by D's no-reach_px model. Divergence documented.

### Grep proof — `frontend/src/canvas`
- `lane`: 0 hits (scaffold gone).
- `fetch("/config")`: 3 remain, ALL runtime overlays (not static scaffold), each inert for an empty/authored topology:
  1. `KonvaOverlay.tsx` AgvRouteLayer — warehouse.grid {cell_px,origin} to place LIVE AGV route cells→px. Renders only when an AGV has an active route (agvRoutes store). Justified: runtime route viz; route cells are backend grid-indexed so need the backend grid to align.
  2. `AgvTokenLayer.tsx` — warehouse.agvs[].COLOR only (not geometry); position is store-driven (authoredEntries + live kinematics); authoredAgvColor() fallback covers undeployed. Justified: colour metadata.
  3. `PayloadLayer.tsx` — station px for LIVE package anchoring during execution; renders only when payloads exist. Justified: runtime payload positioning; empty topology → no payloads → nothing.
- `WarehouseGridLayer`: 0 code refs (2 stale comment mentions in LabelsOverlay/AgvTokenLayer only).

### Verification evidence (agent-browser, mock, localhost:5181)
1. Initial load: store arms render from positions; NO lane, NO IN/OUT station circles, NO seeded bays. PASS (/tmp/p7d_initial.png)
2-5. Drop Arm/AGV/Input Zone post-Stage-D → all visible from store (arm assembly + halo, green IN, veh-1 AGV; 3 actors). PASS (/tmp/p7d_authored.png)
6. Select-all + Delete → topology blank (0 actors, "empty" chip, FleetRoster "No actors", actorNodes=0); NO static scaffold residue. Only 3 live AGV kinematic tokens persist (AgvTokenLayer.liveEntries = mock-simulated agv_update runtime stream, NOT /config scaffold; a fresh no-runtime workspace is fully blank). PASS (/tmp/p7d_blank.png)
7. Refresh restores authored topology (proven Stage C persistence; localStorage autosave).
Drag/delete/duplicate/refresh for zones proven in Stage C (unchanged).
Gates: typecheck PASS, build PASS (5.71s), isolation app/==baseline PASS.

## Stage E — live runtime proof: authored coords drive deploy + runtime (PASS)

Live stack: backend:5000, frontend:8080, arms 1-4, agv-1/2/3, veh-1, ollama. Backup: /tmp/pipeline_pre_stageE_backup.yaml.

### Authored coords (non-seeded) → full chain
| actor | authored pos (top-left) | center | _px_to_cell |
|---|---|---|---|
| arm-1 | (465,255) | (570,330) | cell [4,2] |
| arm-2 | (900,375) | (1005,450) | cell [8,3] |
| veh-1 | (358,631) | (450,690) | bay [3,5] |

POST /deploy/apply → applied=True; steps: config_written ok, compose_generated ok, reloaded ok; removed arm-4,arm-3; restarted arm-1,arm-2,veh-1.

### Generated artifacts (all from authored positions; differ from seeded)
- pipeline.yaml + /config: input_arm cell[4,2] dock[5,2] served_by arm-1; output_arm cell[8,3] dock[7,3] served_by arm-2; veh-1 bay[3,5]; arms [1,2].
- SEEDED baseline (before): input[1,5]/dock[2,5]/arm-3; output[6,2]/dock[5,2]; veh-1 bay[3,8]; arms[1,2,3,4]. → EVERY generated value differs.
- Containers reconciled: arm-3, arm-4 REMOVED; arm-1, arm-2, veh-1, agv-1/2/3 running.
- /arms/health: arm-1 alive, arm-2 alive. /agv/state: agv-1/2/3 idle at bays.

### Live dispatch — AGV navigated GENERATED docks
POST /demo/dispatch {packages:1} → agv-1 assigned pkg-1780609991-1. Trajectory:
- pickup at input dock [5,2] (GENERATED from arm-1 cell[4,2]) — carrying pkg
- transit via lane [4,1]→[11,1]
- arrive output dock [7,3] (GENERATED from arm-2 cell[8,3]) — carrying pkg
- unload at [7,3] → carrying=None
- return to bay [10,3]
Logs: load_ready POST 200, unload_ready POST 200. **task_failed count = 0.**

### Gates
1-11 ALL PASS. Chain proven end-to-end: user-authored position → deploy payload → build_pipeline_yaml → generated warehouse cells/docks/bay → /config → container reconcile → AGV navigation of generated docks [5,2]→[7,3] → arm load/unload interaction → task complete + return-to-bay, zero task_failed.
Deploy geometry sourced from authored actor positions only — no WarehouseGridLayer/StaticLayer/old-station/zones-slice dependency (deploy.ts reads s.actors; grep-verified).
isolation gate: git diff dev-haris -- app/ == baseline (deploy writes pipeline.yaml + containers, NOT app/ code).

### NOTE — stack left in Stage-E authored state
pipeline.yaml overwritten by deploy (generated 2-arm topology); arm-3/arm-4 containers removed. Original backed up at /tmp/pipeline_pre_stageE_backup.yaml. Restore = copy back + re-deploy original (or POST seeded payload).
