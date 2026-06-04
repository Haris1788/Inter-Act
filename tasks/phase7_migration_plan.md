# Phase 7 — Eliminate Static Warehouse Scaffold, Restore Canvas Source-of-Truth

**Status:** AUDIT + PLAN. No code changed. Awaiting approval.
**Reference:** `E:/Inter Act` (Sneh original, canvas-source model).
**Frozen (do NOT touch):** AI planning, dispatch, contract-net, AGV execution, runtime protocol, socket vocab, warehouse routing, `_px_to_cell`, `deploy_bridge` runtime generation. Phase 6 runtime path is frozen.

---

## 1. Architecture audit — the two worlds

**World A (legacy, must die):** `pipeline.yaml → /config → fetch → Konva`. Six `fetch("/config")` calls hydrate canvas geometry. Immovable.

**World B (target):** `canvas store → deploy → build_pipeline_yaml → /config (output only)`.

D runs both simultaneously. Authored actors (World B) coexist with `/config`-seeded scaffold (World A) in the SAME Konva layers, which merge `seeded[]` + `authored[]` arrays. That is the root of every symptom.

E already resolved this: its `KonvaOverlay.tsx:11-13` documents `WarehouseGridLayer`, `StaticLayer`, `ArmLayer` as **GONE**. Those three are exactly what D still ships.

---

## 2. Render-chain audit — every `/config` consumer in D

| # | File:line | Layer/Fn | Pulls from /config | Renders | Verdict |
|---|---|---|---|---|---|
| 1 | `canvas/overlay/KonvaOverlay.tsx:62` | StaticLayer | stations.px, grid, arm colors | arm reach halos | **remove seeded path** |
| 2 | `canvas/overlay/KonvaOverlay.tsx:342` | ArmLayer | stations.px/dock, grid | arm-2 assembly, table, dock | **remove seeded path** |
| 3 | `canvas/overlay/KonvaOverlay.tsx:576` | AgvRouteLayer | grid.cell_px, origin | A* route polyline | keep grid geom only* |
| 4 | `canvas/overlay/WarehouseGridLayer.tsx:53` | WarehouseGridLayer | warehouse (lane, stations, bays) | grey lane, IN/OUT, bays | **delete file** |
| 5 | `canvas/overlay/AgvTokenLayer.tsx:109` | AgvTokenLayer | agvs[*].color | AGV chassis color | **color from actor** |
| 6 | `canvas/overlay/PayloadLayer.tsx:44` | PayloadLayer | stations geometry | package anchors | **anchors from actors** |
| 7 | `canvas/flow/useFlowNodes.ts:30` | useFlowNodes | arms[*].id | konvaOnly filter | **remove; force konvaOnly all** |

\*AgvRouteLayer needs cell_px/origin only for cell→px math of a runtime route. Acceptable as runtime viz IF grid geometry is sourced from the authored grid constant instead of /config. Lowest priority; can keep last.

**Static-geometry layers to eliminate:** StaticLayer (in KonvaOverlay), ArmLayer (in KonvaOverlay), WarehouseGridLayer (whole file).

---

## 3. Zone lifecycle audit — D vs target

| Path | D (broken) | file:line | Target (E model) |
|---|---|---|---|
| storage | separate `zones` slice | topologySlice.ts:14 | `kind:"zone"` in `actors` |
| create | `createZoneFromPalette`→`replaceTopology` | nodeFactory.ts:153, CanvasViewport.tsx:149 | `createActorFromPalette`→`addActor` |
| select | rejected `node.type!=="actorNode"` | CanvasViewport.tsx:91 | all nodes are actorNode → passes |
| drag | `draggable:false` | useFlowNodes.ts:80 | actorNode shell, draggable |
| delete | no `removeZone`; loop `removeActor` only | CanvasViewport.tsx:205 | zones are actors → `removeActor` |
| duplicate | none | — | `duplicateActor` (zones inherit) |
| render | `ZoneNode.tsx` RF card | nodeTypes.ts | `ZoneMarkerLayer` Konva, store-driven |
| serialize | `zones[]` slice | topologySchema.ts:37 | derive from `actors.filter(kind==="zone")` |

**Why zones can't move/delete now:** `draggable:false` + `selectable:false` (useFlowNodes.ts:80-81) + click handler rejects zoneNode (CanvasViewport.tsx:91) + Delete loops only `removeActor` + no `removeZone` mutation exists.

---

## 4. Exact file list — D vs E

### Files only in E (candidate ports)
| File | Role | Migration use |
|---|---|---|
| `canvas/overlay/ArmRenderer.tsx` | store-driven arm (kind=arm actors) | **PORT** → replaces StaticLayer+ArmLayer |
| `canvas/overlay/ZoneMarkerLayer.tsx` | store-driven zone (kind=zone actors) | **PORT** → replaces ZoneNode |
| `canvas/overlay/DynamicGridLayer.tsx` | runtime active-cell dots | **PORT (gated)** → replaces WarehouseGridLayer |
| `canvas/overlay/agv/{ShuttleAgv,ForkliftAgv,TowAgv,types}.tsx` | richer AGV shapes | OPTIONAL (D AgvTokenLayer already store-pos) |
| `canvas/snap/useSnapPreference.ts`, `canvas/topologyBounds.ts` | snap + bounds ergonomics | OPTIONAL |
| `canvas/overlay/{AgvTrailLayer,NegotiationHaloLayer,PathPreviewLayer,ReservedCellsLayer}.tsx` | runtime viz | OPTIONAL, not scaffold |
| `chrome/Observability/*`, `chrome/Replay/*`, `Inspector/SectionSpatial.tsx` | E-only panels | **SKIP** — deliberately removed in D (parity done) |

### Files only in D
| File | Verdict |
|---|---|
| `canvas/overlay/WarehouseGridLayer.tsx` | **DELETE** (obsolete scaffold) |
| `canvas/flow/nodes/ZoneNode.tsx` | **DELETE** (zones become actors) |
| `canvas/flow/nodes/PoolLabelNode.tsx` | **DELETE** (vestigial; pool label already dropped) |
| `chrome/Inspector/SectionTaskQueue.tsx` | KEEP (D parity feature) |

### D files to MODIFY (no new file)
- `store/types.ts` — `ActorKind += "zone"`; `TopologyActor += subtype?: "source"|"sink"`.
- `runtime/nodeFactory.ts` — fold zone into `createActorFromPalette` (ZONE_SIZE, nextZoneId, subtype); drop `createZoneFromPalette`.
- `canvas/flow/useFlowNodes.ts` — every actor → one invisible `actorNode`; drop zone branch + `/config` fetch + konvaOnly logic.
- `canvas/flow/nodeTypes.ts` — drop `zoneNode`, `poolLabelNode`.
- `canvas/flow/nodes/ActorNode.tsx` — pure invisible shell (E model) OR keep konvaOnly path forced true for all.
- `canvas/CanvasViewport.tsx` — `onDrop` single path via `createActorFromPalette`+`addActor`; delete/duplicate/select already unify once zones are actors.
- `canvas/overlay/KonvaOverlay.tsx` — remove StaticLayer + ArmLayer; mount ArmRenderer + ZoneMarkerLayer; swap WarehouseGridLayer → DynamicGridLayer (or drop).
- `canvas/overlay/AgvTokenLayer.tsx` — color from actor, drop `/config` fetch.
- `canvas/overlay/PayloadLayer.tsx` — station anchors from actors (kind=arm) not `/config`.
- `lib/validation.ts` — source/sink checks read `actors.filter(kind==="zone")`.
- `lib/topologySchema.ts` — serialize/deserialize zones via actors (compat shim for old `zones[]` docs).
- `runtime/deploy.ts` — `buildDeployPayload` emits zone actors with position (additive; backend ignores until needed — stays frozen).
- `runtime/configNormalizer.ts`, `runtime/presets.ts`, `runtime/mockData.ts`, `runtime/mockDriver.ts` — seed zones as `kind:"zone"` actors.
- `store/selectors.ts`, `store/derived.ts`, `runtime/history.ts`, `runtime/useAutosave.ts`, `lib/contract.ts` — zone reads derive from actors; retire `zones` slice tracking.

---

## 5. Migration sequence (staged, each stage builds + typechecks green)

**Stage A — schema (no visual change).** types.ts ActorKind+subtype. nodeFactory zone-as-actor. Keep `zones` slice readable for back-compat. Build green.

**Stage B — unify RF nodes.** useFlowNodes: all actorNode invisible shells; drop zone branch + /config konvaOnly fetch. ActorNode → invisible shell. nodeTypes drop zoneNode/poolLabelNode. CanvasViewport onDrop single path. Now zones drag/select/delete/duplicate for free. Delete ZoneNode.tsx, PoolLabelNode.tsx.

**Stage C — store-driven arm render.** Port ArmRenderer.tsx (kind=arm actors, authored position, armAngles from store). Mount in KonvaOverlay. Remove StaticLayer + ArmLayer. Force-render all arms (seeded + authored) store-driven.

**Stage D — store-driven zone render.** Port ZoneMarkerLayer.tsx (kind=zone actors). Mount in KonvaOverlay.

**Stage E — kill static grid.** Delete WarehouseGridLayer.tsx. Port DynamicGridLayer.tsx (runtime dots) IF runtime deps exist (see risk R3); else mount nothing — blank canvas until authored. AgvTokenLayer + PayloadLayer source from actors, drop /config.

**Stage F — serialization + seed paths.** validation/topologySchema/configNormalizer/presets/mockData/contract/history/autosave migrate zones→actors. Retire `zones` slice. Build + full typecheck.

**Stage G — verify.** typecheck + vite build. Browser: empty canvas = blank (no scaffold). Drop arm/AGV/zone → render store-driven. Drag/delete/duplicate zone. Deploy → /config is output. AI Plan → Dispatch unchanged (frozen path).

---

## 6. Risk analysis

- **R1 — ActorNode card loss.** D's card carries inline label-edit, bid pill, coherence dot. E's is invisible. *Mitigation:* move label-edit to Inspector (already store-backed); bid/coherence move to Konva or are non-critical. Confirm no feature regress before deleting card.
- **R2 — `zones` slice retirement breaks back-compat.** Old localStorage workspaces have `zones[]`. *Mitigation:* `docToState` shim converts legacy `zones[]` → `kind:"zone"` actors on load. Additive, idempotent.
- **R3 — DynamicGridLayer runtime deps.** It needs `useActorRuntimeAll`, `useAgvPaths` selectors + `/reservations` endpoint + CELL_PX. D may lack these; E uses CELL_PX=40 vs D warehouse cell_px=120. *Mitigation:* DynamicGridLayer is OPTIONAL. If deps absent, mount nothing (blank canvas honors "empty world = blank"). Do NOT add runtime endpoints (frozen). Source cell_px from authored grid constant, not /config.
- **R4 — Backend still seeds /config warehouse.** After deploy, /config has warehouse. If any layer re-reads it, World A resurrects. *Mitigation:* Stage E removes ALL canvas /config geometry reads. Grep-gate: zero `fetch("/config")` for geometry post-migration.
- **R5 — deploy_bridge expects arms→stations (frozen).** Zones don't generate warehouse geometry; arms do (first/last → input/output station). *Mitigation:* zones stay frontend-only semantic markers; deploy payload zone positions are additive and ignored by frozen backend. No deploy_bridge change.
- **R6 — Seeded arm visual parity.** Removing ArmLayer means seeded arms (arm-1/2) render via ArmRenderer too. *Mitigation:* hydrate seeded arms into `actors` as `kind:"arm"` with authored=false but SAME render path (E does this). Verify halo/assembly parity.

---

## 7. Rollback strategy

- Git branch `phase7-canvas-sot` off `dev-haris`. One commit per stage (A–G).
- Each stage independently revertable (`git revert <stage>`); stages ordered so a revert of stage N+1 leaves N green.
- Frozen backend untouched → no runtime rollback needed; `git checkout dev-haris -- app/` proves zero backend delta.
- Tag pre-migration HEAD. If World-A resurfaces, `git reset --hard <tag>` restores scaffold instantly.
- Keep `WarehouseGridLayer.tsx` deletion as the LAST file op so an early abort still has the scaffold.

---

## 8. How canvas becomes single source of truth

Post-migration the only inputs to canvas rendering are `store.actors` (incl. kind=zone) + `store.actorRuntime` (live motion). No layer reads `/config` for geometry. Authoring flow:

```
drag arm/agv/zone → createActorFromPalette → addActor → actors[]
   → ArmRenderer / AgvTokenLayer / ZoneMarkerLayer (store-driven)
   → Deploy → buildDeployPayload(actors) → build_pipeline_yaml (FROZEN)
   → /config (OUTPUT) → runtime actors → AI Plan → Dispatch → Execution (FROZEN)
```

`/config` flips from input to output. Empty store = blank canvas (no scaffold). Zones are ordinary actors. Deploy converts authored coordinates to warehouse geometry via the already-proven `_px_to_cell` chain (Phase 6). World A is deleted; only World B remains.
