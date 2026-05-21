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
