# Phase 7 — Staged Migration Spec (Canvas Source-of-Truth)

**Status:** SPEC ONLY. No code until reviewed + approved.
**Reference architecture:** `E:/Inter Act` (Sneh original — dynamic coordinate authoring, actor-based rendering, draggable/deletable zones, canvas-as-source-of-truth).
**Target branch:** `D:/Inter Act` (`dev-haris`).
**Locked decisions:** (1) Grid = **blank canvas**, no DynamicGridLayer port. (2) ActorNode = **invisible shell** (E model), all visuals in Konva. (3) Deliver spec, then **stop**.

**FROZEN — never modified this phase:** AI planning, dispatch, contract-net, AGV execution, runtime protocol, socket vocabulary, warehouse routing, `_px_to_cell`, `deploy_bridge` runtime generation. Backend = zero delta. Migration is frontend-only.

**Branch:** all work on `phase7-canvas-sot` off `dev-haris`. One commit per stage. Tag `phase7-pre` at HEAD before Stage A.

---

## GLOBAL INVARIANTS (apply to EVERY stage A–F)

### Frontend Runtime Isolation Rule

Throughout all stages, NONE of the following may change:
- No backend files modified.
- No `deploy_bridge` changes.
- No `pipeline.yaml` generation changes.
- No AGV routing changes.
- No dispatch changes.
- No contract-net changes.
- No runtime container changes.

**Verification gate after EVERY stage:**
```
git diff dev-haris -- app/
```
**Expected result: no changes.** A non-empty diff = stage FAIL → revert the offending edit before proceeding.

**Purpose:** Phase 7 is strictly a frontend architecture migration. Any runtime behavior change discovered during the migration must be documented separately (in `tasks/todo.md` review section) and **NOT** fixed within Phase 7. Runtime remains frozen; the frontend source-of-truth model is the only target.

> Scope note: Stage E touches `runtime/deploy.ts` + `chrome/DeployModal.tsx` (frontend payload/display only) — those are NOT under `app/`, so the gate still expects an empty `app/` diff. `deploy_bridge.py` is referenced read-only.

### E:/Inter Act Reference Rule

Before implementing ANY renderer, zone behavior, actor behavior, or topology interaction behavior:
1. Compare against the equivalent implementation in `E:/Inter Act`.
2. If D and E differ: **document the difference** and **explain why E's behavior is or is not being adopted** (record in the stage's commit message + `tasks/todo.md`).
3. Do NOT silently reimplement from memory.

`E:/Inter Act` is the behavioral reference for: actor rendering, zone rendering, draggable zones, deletable zones, canvas interaction, live coordinate behavior, canvas-as-source-of-truth architecture. Each stage's checklist below carries an explicit "E-diff documented" gate.

---

## Ordering principle

Stages A–B are additive/structural (old zone path kept alive until B flips it). Full visual correctness lands at end of **Stage C**. There is ONE documented transient: between Stage B and Stage C, zones render invisibly (RF shell exists, Konva marker not yet mounted). This is expected and green-building. Scaffold is removed only at Stage D, after the store-driven renderers prove out at C.

---

## Stage A — Zone actor unification (additive schema, zero behavior change)

### Exact files
- `store/types.ts`
- `lib/actorNormalizer.ts`
- `runtime/nodeFactory.ts`
- `lib/topologySchema.ts`

### Exact responsibilities
1. `types.ts`: `ActorKind = "arm" | "agv" | "zone"`. Add `subtype?: "shuttle" | "forklift" | "tow" | "source" | "sink"` to `TopologyActor`. Keep `TopologyZone` type + `zones` slice field intact (deprecated, still populated this stage).
2. `actorNormalizer.ts`: teach `normalizeActor` the `zone` kind — default `ZONE_SIZE = {w:100,h:100}`, `capabilities: []`, label derive ("Zone Source 1" / "Zone Sink 1"), carry `subtype`. Match E's normalizer defaults.
3. `nodeFactory.ts`: add `ZONE_SIZE`, `nextZoneId(subtype)`; extend `createActorFromPalette` to handle `kind:"zone"` (E lines 110-145). PALETTE `zone-source`/`zone-sink` gain `kind:"zone"` + `subtype`. **Do NOT yet remove `createZoneFromPalette`** — onDrop still calls it this stage.
4. `topologySchema.ts` `docToState`: add a **legacy shim** — when a loaded doc has `zones[]`, additionally materialize each as a `kind:"zone"` actor (idempotent; dual-present is fine because Stage B removes the zoneNode render). Old workspaces auto-upgrade on load.

### Expected UI change
**None.** Zones still created via the old `createZoneFromPalette` → `zones` slice → `ZoneNode` card. New actor-zone capability is dormant (nothing calls the new path yet).

### Expected runtime impact
None. No socket/deploy/backend interaction. Pure type + factory extension.

### Rollback path
`git revert <A>`. Schema additions are additive; reverting removes the unused zone-kind capability with no downstream dependency yet.

### Verification checklist
- [ ] `tsc --noEmit` green.
- [ ] `vite build` green.
- [ ] Drop a zone → still appears as before (old path), no visual regression.
- [ ] Load a legacy workspace with `zones[]` → no crash; zone actors silently materialized (inspect store: `actors` contains `kind:"zone"` entries).
- [ ] Existing arm/AGV drop unchanged.
- [ ] `git diff dev-haris -- app/` empty.
- [ ] E-diff documented (zone-as-actor schema vs E `types.ts`/`nodeFactory.ts`).

---

## Stage B — ActorNode invisible-shell migration

### Exact files
- `canvas/flow/nodes/ActorNode.tsx`
- `canvas/flow/useFlowNodes.ts`
- `canvas/flow/nodeTypes.ts`
- `canvas/CanvasViewport.tsx`

### Exact responsibilities
1. `ActorNode.tsx`: replace card with E's invisible 1×1 shell (`opacity:0`, `pointerEvents:none`). RF keeps it for drag/pan/zoom/selection math; all visuals defer to Konva. Inline label-edit moves to Inspector (already store-backed via `updateActor`).
2. `useFlowNodes.ts`: emit ONE `actorNode` per actor for every kind (arm/agv/zone). Drop the zone branch, the `seededArmIds` `fetch("/config")`, and `konvaOnly` logic entirely (E lines 11-34). Every node draggable + selectable.
3. `nodeTypes.ts`: remove `zoneNode` and `poolLabelNode` entries (keep only `actorNode`).
4. `CanvasViewport.tsx`: `onDrop` → single path `createActorFromPalette` + `addActor` + select (drop the `createZoneFromPalette`/`replaceTopology` zone branch, CanvasViewport.tsx:149-163). `handleNodeClick`/`handleNodeContextMenu` already pass (all nodes are `actorNode`). Delete + duplicate loops already operate on `selectedIds` → zones now inherit move/select/delete/duplicate automatically.

### Expected UI change
- Arms/AGVs lose their RF card chrome; the existing Konva assemblies (authored path) remain. Seeded arm-1/arm-2 cards disappear (their Konva still drawn by the soon-removed ArmLayer until Stage D).
- **Transient (B→C):** newly dropped zones become actors but have NO Konva marker yet → invisible until Stage C mounts ZoneMarkerLayer. Old seeded/mock zones also vanish (zoneNode removed). **Expected.**
- Zones are now draggable/selectable/deletable/duplicatable (verifiable via store even while visually pending).

### Expected runtime impact
None. The dropped `fetch("/config")` in useFlowNodes was a read-only geometry call; removing it touches no runtime protocol.

### Rollback path
`git revert <B>`. Restores card + zoneNode + old onDrop. Stage A remains green underneath.

### Verification checklist
- [ ] `tsc --noEmit` + `vite build` green.
- [ ] Drop zone → store shows new `kind:"zone"` actor; select/drag/Delete/Ctrl-D all act on it (confirm via store/inspector even though marker not yet visible).
- [ ] Arm/AGV drag still updates `actor.position`.
- [ ] No `fetch("/config")` remains in `useFlowNodes.ts`.
- [ ] Inspector label-edit works for all kinds.
- [ ] `git diff dev-haris -- app/` empty.
- [ ] E-diff documented (invisible-shell ActorNode + unified useFlowNodes vs E).

---

## Stage C — Konva renderer migration (ArmRenderer + ZoneMarkerLayer)

### Exact files
- **NEW** `canvas/overlay/ArmRenderer.tsx` (port from E)
- **NEW** `canvas/overlay/ZoneMarkerLayer.tsx` (port from E)
- `canvas/overlay/KonvaOverlay.tsx` (mount both)
- `runtime/configNormalizer.ts`, `runtime/presets.ts`, `runtime/mockData.ts`, `runtime/mockDriver.ts` (seed zones as `kind:"zone"` actors)

### Exact responsibilities
1. Port `ArmRenderer.tsx`: render one Konva group per `kind:"arm"` actor at authored `position` (pivot, rotating stick from `armAngles` store, gripper, reach ring, label). Source angles from D's existing `useArmAngles` selector. Adapt E's reach/geometry to D's tokens. **No `/config` read** — store only.
2. Port `ZoneMarkerLayer.tsx`: render one labeled circle per `kind:"zone"` actor (`subtype` → IN/OUT color + label), at `position + size/2`. `listening={false}` (RF handles drag/select). Store-driven via `useActors().filter(kind==="zone")`.
3. `KonvaOverlay.tsx`: mount `<ArmRenderer/>` + `<ZoneMarkerLayer/>` as sibling Layers. **Do NOT yet remove StaticLayer/ArmLayer** — Stage D does that (keeps each stage green; brief double-arm-render for seeded arms is the only overlap, removed next stage).
4. Seed paths emit zones as `kind:"zone"` actors instead of `zones` slice entries (so ZoneMarkerLayer sees seeded/mock/preset zones). `configNormalizer` intake/output → zone actors; `presets`/`mockData` `ZONES` → actor entries; `mockDriver` hydrate routes zones into `actors`.

### Expected UI change
- Authored + seeded arms render via store-driven ArmRenderer.
- Zones now VISIBLE again (Konva markers), draggable/deletable. Transient from Stage B resolved.
- Possible brief double-render of seeded arms (old ArmLayer + new ArmRenderer) — removed in Stage D.

### Expected runtime impact
None. Renderers are store-driven; seed-path changes alter only the frontend store shape, not socket/deploy.

### Rollback path
`git revert <C>` (drops new layers + seed migration). Delete the two new files. Stages A–B stay green.

### Verification checklist
- [ ] `tsc --noEmit` + `vite build` green.
- [ ] Drop arm → store-driven assembly at drop point.
- [ ] Drop Input/Output Zone → IN/OUT marker visible, draggable, deletable, duplicatable.
- [ ] Mock mode: seeded zones appear as actor markers.
- [ ] Neither new file contains `fetch("/config")`.
- [ ] `git diff dev-haris -- app/` empty.
- [ ] E-diff documented (ArmRenderer + ZoneMarkerLayer port deltas vs E; geometry/token adaptations noted).

---

## Stage C.5 — Source-of-Truth Verification Gate (MANDATORY, BLOCKING)

**Purpose:** Prove the canvas is fully topology-driven BEFORE deleting the remaining scaffold. Stage D may NOT begin until C.5 passes. This gate exists because the renderers (C) and the scaffold (pre-D) coexist at this point — C.5 forces proof that the store-driven path stands alone, so removing the scaffold at D is subtraction of dead pixels, not a leap of faith.

### Exact files (read-only audit — NO code change in this stage)
- All render-layer files: `canvas/overlay/*.tsx`, `canvas/flow/**/*.tsx`, `canvas/labels/*.tsx`.

### Verification requirements
1. Blank canvas loads with **zero** warehouse geometry (no lane, no IN/OUT, no static arm).
2. User can create: Arm, AGV, Input Zone, Output Zone.
3. User can: drag, delete, duplicate, save, refresh (round-trip survives reload).
4. All visuals originate from topology actors only.
5. No visual element depends on `/config`.
6. No visual element depends on `warehouse.stations`.
7. No visual element depends on `warehouse.agvs`.
8. No visual element depends on `warehouse.lane`.

> NOTE on temporary scaffold: at C.5 the StaticLayer/ArmLayer/WarehouseGridLayer still exist (removed at D). To prove requirement 1 + 5–8 cleanly, run the gate against a **backend returning an empty/absent `warehouse` section** (or `/config` stubbed empty). With no seeded warehouse, the scaffold layers render nothing, so any geometry on screen MUST come from actors. This isolates the store-driven path without yet deleting the scaffold code. The grep gates (below) separately prove which files still TOUCH `/config` so Stage D knows exactly what to cut.

### Required evidence (attach to the stage commit / PR)
- **Screenshots:** (a) blank canvas, empty store; (b) canvas after authoring Arm A + Arm B + AGV + Input Zone + Output Zone; (c) same after a browser refresh (persistence proof); (d) after deleting + duplicating one zone.
- **Component trace:** for each visible element, the Konva layer + selector that produced it (e.g. "IN marker ← ZoneMarkerLayer ← useActors().filter(kind==='zone')"). Must show ZERO `/config`-sourced visuals.
- **Actor lifecycle trace:** store snapshot (JSON) of `actors` at: create → drag (position delta) → duplicate (new id) → delete (id gone) → save → reload (rehydrated identical).
- **Grep results:** outputs of the four gates below, annotated per hit as either (i) a render layer that must be cut at Stage D, or (ii) a non-render module out of scope.

### Frontend grep gates (run across render-layer files)
```
grep -rn 'fetch("/config")'   frontend/src/canvas
grep -rn 'warehouse\.'        frontend/src/canvas
grep -rn 'stations'           frontend/src/canvas
grep -rn 'lane'               frontend/src/canvas
```
Expected at C.5: hits ONLY in the three scaffold layers slated for Stage D deletion (StaticLayer + ArmLayer inside `KonvaOverlay.tsx`, `WarehouseGridLayer.tsx`) plus possibly `AgvTokenLayer`/`PayloadLayer`/`AgvRouteLayer`. NO hits in `ArmRenderer.tsx`, `ZoneMarkerLayer.tsx`, `useFlowNodes.ts`, `ActorNode.tsx`. Any unexpected hit = gate FAIL → fix before Stage D.

### Pass criteria
- [ ] All 8 verification requirements met with attached evidence.
- [ ] Four grep gates produce only the expected scaffold-layer hits; the new store-driven renderers are clean.
- [ ] Empty-warehouse `/config` run shows authored actors rendering and zero scaffold geometry.
- [ ] Sign-off recorded in `tasks/todo.md` before Stage D starts.

**BLOCKING:** Stage D does not begin until every box above is checked.

---

## Stage D — Static scaffold removal

### Exact files
- `canvas/overlay/KonvaOverlay.tsx` (delete StaticLayer + ArmLayer definitions + mounts)
- `canvas/overlay/WarehouseGridLayer.tsx` (**DELETE FILE**)
- `canvas/overlay/AgvTokenLayer.tsx` (color from actor, drop `/config`)
- `canvas/overlay/PayloadLayer.tsx` (station anchors from `kind:"arm"` actors, drop `/config`)

### Exact responsibilities
1. `KonvaOverlay.tsx`: remove `StaticLayer` and `ArmLayer` functions + their JSX mounts and the two `fetch("/config")` calls (lines 62, 342). Remove `WarehouseGridLayer` import/mount. **Decision: grid = blank** — mount no replacement. (AgvRouteLayer's grid-geom `/config` read at line 576 may remain as runtime route viz, or be sourced from an authored grid constant; lowest priority, flag for Stage F if it still reads /config.)
2. **Delete** `WarehouseGridLayer.tsx`.
3. `AgvTokenLayer.tsx`: derive chassis color from the actor (palette/`authoredAgvColor`), remove the `/config` color fetch (line 109).
4. `PayloadLayer.tsx`: compute station/table/dock anchors from `kind:"arm"` actors' authored positions (same geometry math, store source), remove `/config` fetch (line 44).

### Expected UI change
- Grey lane, IN/OUT scaffold markers, seeded static arm assemblies all **gone**.
- Empty store ⇒ blank canvas. Everything visible is now an authored/seeded actor.
- Packages + AGV tokens position from store/actors (parity preserved).

### Expected runtime impact
None to the frozen path. `/config` is no longer consumed for canvas geometry (verify with grep gate). Backend still emits `/config`; nothing reads it for rendering.

### Rollback path
`git revert <D>` restores scaffold layers + WarehouseGridLayer. Keep this stage's commit isolated so an early abort still has the scaffold. This is the highest-visual-impact stage — verify thoroughly before proceeding.

### Verification checklist
- [ ] `tsc --noEmit` + `vite build` green.
- [ ] Empty canvas = blank (no lane, no IN/OUT, no phantom arm).
- [ ] `grep -rn 'fetch("/config")' canvas/` → only AgvRouteLayer remains (or zero).
- [ ] AGV token color correct from actor.
- [ ] Package render parity (drop on table / on AGV / at output).
- [ ] `git diff dev-haris -- app/` empty.
- [ ] E-diff documented (scaffold-removal parity vs E's "GONE" layers).

---

## Stage E — Deploy / runtime verification (no scaffold dependency)

### Exact files
- `runtime/deploy.ts` (`buildDeployPayload` — additive zone positions)
- `chrome/DeployModal.tsx` (zone count from actors — display only)
- *(read-only verification of)* `app/services/deploy_bridge.py` (FROZEN — confirm zero change)

### Exact responsibilities
1. `deploy.ts`: include `kind:"zone"` actors with `position` + `subtype` in the payload (additive). Arms still drive station generation in the frozen backend; zone positions are forward-compat metadata the backend currently ignores. **No backend change.**
2. `DeployModal.tsx`: derive the zone count from `actors.filter(kind==="zone")` rather than the `zones` slice (display only).
3. Verify end-to-end on the live stack: author topology → Deploy → `pipeline.yaml` regenerated from authored coordinates (Phase 6 `_px_to_cell` chain) → `/config` is an OUTPUT → AI Plan → Dispatch → Execution all unchanged.

### Expected UI change
Deploy modal shows authored actor/zone counts. No canvas change.

### Expected runtime impact
**Zero to the frozen path.** Deploy payload gains zone fields (ignored by backend). `build_pipeline_yaml` / warehouse generation / routing unchanged. Confirm `git diff dev-haris -- app/` is empty.

### Rollback path
`git revert <E>`. Payload zone fields are additive; reverting is inert (backend never depended on them).

### Verification checklist
- [ ] `git diff dev-haris -- app/` empty (backend frozen).
- [ ] Deploy authored topology → `pipeline.yaml` `warehouse.stations` derive from authored arm coords (re-run Phase 6 proof).
- [ ] `/config` reflects authored topology (output).
- [ ] AI Plan → Dispatch → Execution complete; zero `task_failed` regressions vs baseline.
- [ ] No layer re-hydrates canvas from the new `/config` (World A stays dead).

### Stage E — Non-Default Coordinate Runtime Proof (MANDATORY)

**Why:** Phase 6 evidence partly used coordinates that mapped to the SAME cells as the seeded topology (arm-1→[1,1], arm-2→[12,1]) — so a pass could not distinguish "authored coords drive runtime" from "authored coords coincidentally equal seeded coords." This proof eliminates the coincidence by authoring positions whose generated cells are provably DIFFERENT from every seeded value.

**Setup — author intentionally non-seeded positions:**
- Arm A, Arm B, AGV A, Input Zone, Output Zone.
- All placed at canvas coordinates whose `_px_to_cell` output differs from the seeded warehouse:
  - seeded stations: input `cell[1,5] dock[2,5]`, output `cell[6,2] dock[5,2]` (current working-tree pipeline.yaml).
  - seeded bays: `[10,3] [10,5] [10,7]`, `veh-1 [3,8]`.
- Choose drop points so generated `cell`/`dock`/`bay` land on NONE of the above (e.g. Arm A → `cell[4,2]`, Arm B → `cell[8,3]`, AGV A → `bay[3,5]` — the values the prior re-audit already computed as distinct).

**Deploy, then verify (11 gates):**
1. Generated station cells differ from seeded cells.
2. Generated dock cells differ from seeded docks.
3. Generated AGV bay differs from seeded bays.
4. `pipeline.yaml` reflects the authored coordinates.
5. `/config` reflects the generated coordinates.
6. Runtime containers start successfully.
7. AI Plan executes successfully.
8. Dispatch executes successfully.
9. AGV navigates using the generated coordinates (route cells == generated dock/bay path, not seeded).
10. Zero `task_failed` events.
11. Return-to-bay completes successfully.

**Required evidence (attach):**
- authored coordinates (store snapshot: each actor's `position` + `size`).
- generated cells / docks / bay (the written `pipeline.yaml warehouse` section + the `_px_to_cell` mapping for each actor).
- runtime route (AGV `path_cells` from the bus / `agvRoutes`, shown to traverse the generated dock cells).
- task completion (log/bus trace: announce → bid → accept → load → transport → unload → return-to-bay, zero `task_failed`).
- side-by-side table: authored coord → generated cell vs seeded cell (column proving inequality on every row).

**Goal:** prove runtime behavior originates from authored canvas coordinates, not from coincidental equality with the original seeded topology.

> Live-stack dependency: gates 6–11 need Docker + the LLM tunnel. If unavailable in the dev environment, gates 1–5 (deterministic, runnable offline via `build_pipeline_yaml`) MUST still pass and be evidenced; gates 6–11 are then executed on the live stack and their evidence appended before Stage E sign-off. Do not mark Stage E complete on offline evidence alone.

---

## Stage F — Cleanup + dead-code removal

### Exact files
- `canvas/flow/nodes/ZoneNode.tsx` (**DELETE**)
- `canvas/flow/nodes/PoolLabelNode.tsx` (**DELETE**)
- `store/types.ts` (retire `TopologyZone` + `zones` slice field)
- `store/topologySlice.ts` (drop `zones` initial state)
- `store/selectors.ts` (`useZones` → derive from actors, or remove)
- `store/derived.ts`, `runtime/history.ts`, `runtime/useAutosave.ts` (zone tracking → actors)
- `lib/validation.ts` (source/sink checks via `actors.filter(kind==="zone")`)
- `lib/topologySchema.ts` (serialize zones from actors; keep legacy `zones[]` read shim for import)
- `lib/contract.ts` (zone owner resolution from actors)
- `runtime/nodeFactory.ts` (remove now-unused `createZoneFromPalette`)
- `runtime/configNormalizer.ts`, `runtime/presets.ts`, `runtime/mockData.ts` (drop residual `zones`-slice emission)

### Exact responsibilities
Retire the legacy `zones` slice entirely. All zone reads/writes/serialize/validate route through `actors` (kind=zone). Keep ONLY a one-way legacy import shim in `topologySchema` so old `zones[]` workspaces still load. Remove dead components (ZoneNode, PoolLabelNode) and the unused `createZoneFromPalette`. Final grep gate: no `zones` slice references remain except the import shim.

### Expected UI change
None (functional parity already reached at D). Pure internal cleanup.

### Expected runtime impact
None.

### Rollback path
`git revert <F>`. Functionality already complete at E; F is non-functional cleanup, safest to revert.

### Verification checklist
- [ ] `tsc --noEmit` + `vite build` green.
- [ ] `grep -rn "TopologyZone\|\.zones\|zoneNode\|createZoneFromPalette" frontend/src` → only the legacy import shim.
- [ ] Save → reload → zones persist as actors (round-trip).
- [ ] Import a legacy `zones[]` workspace → zones load as actors.
- [ ] validation source/sink warnings still fire correctly.
- [ ] Full frontend test/typecheck/build clean; `git diff dev-haris -- app/` still empty.
- [ ] E-diff documented (does E retire its `zones` slice or keep it? note divergence + rationale).

---

## Single-source-of-truth end state

```
drag arm / agv / input-zone / output-zone
  → createActorFromPalette → addActor → store.actors[] (kind: arm|agv|zone)
  → ArmRenderer / AgvTokenLayer / ZoneMarkerLayer  (store-driven, ZERO /config)
  → Deploy → buildDeployPayload(actors) → build_pipeline_yaml [FROZEN]
  → /config (OUTPUT) → runtime actors → AI Plan → Dispatch → Execution [FROZEN]
```

`/config` flips input→output. Empty store = blank canvas. Zones behave exactly like arms/AGVs (drag/select/delete/duplicate/serialize/import-export). World A deleted; only World B remains. Backend untouched.

---

**END OF SPEC. Do not implement. Awaiting review + approval.**
