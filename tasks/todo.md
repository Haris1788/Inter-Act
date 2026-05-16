# Phase 13 — Operational completion + regression fixes

Backend changes allowed (operational only). No orchestration redesign, no AI, no fake controls.

## Root causes (verified by reading code)

1. **Label regression**: `runtime/nodeFactory.ts:95` hard-sets `label: id`; `ActorNode.tsx:292` + `FleetRoster` render raw `actor.label` (not `displayName()`). No single normalizer — presets/hydration/drag each set label ad hoc.
2. **Inspector regression**: `chrome/Inspector/index.tsx:42` bails on `!runtime`. Drag-create `addActor` never seeds `actorRuntime[id]` → "Select an actor". Not a preset special-case.
3. **MiniMap blank**: node size lives only in `node.style` (`useFlowNodes.ts:25`); `onlyRenderVisibleElements:true` culls off-screen nodes so RF never measures them → MiniMap draws zero-area rects. No CSS clip.
4. **Operational controls**: clear/pause exist (visible-buffer only — correct). Missing: real stop/restart container. `ActionsRow` buttons dead. `LogsHead` shows fake `runtime #284` pill.
5. **Container control**: backend has `/arms/<n>/stop|start`, `/assets/<id>/fail|recover`. Missing `/arms/<n>/restart`, `/runtime/status`.
6. **Panel coherence**: 5 tabs already distinct datasets/renderers. Gaps = fake pill, no per-actor controls, raw ids in roster.
7. **Sync**: `socketDriver` re-bootstraps on connect (good). Ops must reconcile from backend, not advance optimistically.

## Tasks

- [x] Canonical `normalizeActor()` + shared `canonicalLabel(id)`; route drag/hydration/duplicate/import/restore/presets through it
- [x] Render `displayName()` in ActorNode + FleetRoster
- [x] Inspector schema-driven: drop `!runtime` gate, `DEFAULT_RUNTIME` fallback (exported)
- [x] MiniMap: explicit top-level `width`/`height` on nodes in `useFlowNodes`
- [x] Backend: `POST /arms/<n>/restart`, `GET /runtime/status`
- [x] Driver: `stopActor`/`restartActor`/`refreshRuntime` (socket + mock) via `runtime/ops.ts` seam
- [x] Wire ActionsRow + FleetRoster controls; replace fake pill with real `dataSource·linkStatus`
- [x] Reconcile from backend after ops (no optimistic lifecycle advance)
- [x] Verify (frontend typecheck/build + pytest), update docs, local commit (no push)

## Review

All 7 issue groups closed. Root causes were precise single-points, not broad rewrites:

- **Labels**: one `label: id` line + raw-label renders. Fixed by a single canonical normalizer every creation path now uses; renders use `displayName()`.
- **Inspector**: a `!runtime` gate, not a preset special-case (the audit's first hypothesis was wrong — verified `useActorRuntime` returns `undefined` for drag-created actors because `addActor` never seeds runtime). Fixed with `DEFAULT_RUNTIME` fallback; no store seeding (would have regressed coherence draft/deployed semantics).
- **MiniMap**: RF v12 reads node dims from first-class `width`/`height`; style-only + `onlyRenderVisibleElements` culling left it blank. No CSS issue.
- **Controls/backend/sync**: backend already had stop/start/fail/recover — only `restart` + `runtime/status` were missing. Added an ops seam so the UI stays driver-agnostic; live ops reconcile from backend (no optimism); mock stays honest.

Verification: frontend typecheck + build clean; 135/135 pytest (3 new). **Browser smoke not run** — no live Docker stack in this environment; must be done before declaring runtime behavior proven (see HANDOFF.md).

Not done: local-only commit per policy (no push); `graphify update` deferred (frontend not previously graphed).
