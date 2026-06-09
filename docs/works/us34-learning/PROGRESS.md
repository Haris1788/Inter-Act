# US 34 — Implementation Progress

> **Live tracker. Updated after every phase.** Resumable if this session ends.
> Working on branch `dev-haris`. No git operations until the human reviews.

## Feature flag

Reusing `COORDINATION_MODE` (the US 32 flag), extending it with a third value:

| Value | Meaning |
|---|---|
| `centralized` | Legacy path (`central_brain.reassign` + `plan_executor.dispatch_plan`). US 34 features OFF. |
| `decentralized` | US 32 contract-net via bus. US 34 features OFF. **Verified US 32 fallback.** |
| `smart` | US 32 contract-net via bus + **US 34 capability-aware bidding, distance scoring, sequence preservation, A\* routing.** |

Helper `coordination_mode_is_smart()` exposes the boolean to all consumers.

Default in `pipeline.yaml` will be `smart` during the implementation/test cycle; revert to `decentralized` if needed.

---

## Phase checklist

- [x] Read US 34 spec + repo structure
- [x] Write PROGRESS.md (this file)
- [x] Write UI-INTEGRATION-DEFERRED.md
- [x] **Phase 1 (M1)** — AGVCapabilities schema + plumbing ✅ verified live
  - [x] `app/models/agv_capabilities.py` dataclass + pipeline reader
  - [x] `pipeline.yaml` adds `capabilities` per AGV + `ai.capability_defaults` + `ai.bid_weights` + 3rd flag value `smart`
  - [x] `scripts/generate_compose.py` propagates capability env vars per AGV container
  - [x] `agv_state.register` accepts capabilities + stores them
  - [x] `/agv/<id>/state` surfaces capability fields
  - [x] `/coord/snapshot` surfaces capability fields under `agvs.<id>.capabilities`
  - [x] AGV container `/status` surfaces capabilities + coordination_mode
  - [x] AGV container `/capabilities` endpoint
  - [x] BusListener gate widened from `== "decentralized"` to `in (decentralized, smart)`
  - [x] All 7 agv_brain.py mode checks widened similarly
  - [x] /plan route in ai_routes widened similarly
  - [x] **Live test:** `curl /agv/agv-1/state` returns capabilities ✓; `/coord/snapshot` includes them ✓; `/status` shows mode=smart ✓; backend log shows `COORDINATION_MODE=smart`; container log shows `smart (US 34) BusListener started`
  - [x] Learning doc `phase-1.md`
- [x] **Phase 2 (M2)** — Deterministic composite bid ✅ verified live
  - [x] `scripts/agv_bus.py:_ask_for_bid` computes `score = w_d*(1−dist) + w_c*cap_match + w_l*(1−load)` deterministically
  - [x] qwen14b is consulted as a JUDGE; parser alias maps `{accept, adjusted_score}` to legacy `{bid, score}`
  - [x] Weights tunable in `pipeline.yaml:ai.bid_weights`
  - [x] Falls back to `_legacy_us32_ask_for_bid` when `coordination_mode != "smart"`
  - [x] AI-unreachable path: publishes deterministic-only score, doesn't drop bid
  - [x] **Live test:** 3-package run; closest AGV (dist=1, score=92.27) wins pkg-3; spread routes pkg-1/pkg-2; transcripts in `phase-2.md`
  - [x] Learning doc `phase-2.md`
- [x] **Phase 3 (M3)** — Master capability filter ✅ verified live
  - [x] `MasterAnnouncer.on_bid` rejects bids whose AGV's `allowed_labels` lacks the task's label
  - [x] Filter wrapped in try/except — must never block protocol
  - [x] Bid is logged as `REJECT bid ... not in allowed_labels=...` (not silently dropped)
  - [x] Gated on `coordination_mode_is_smart()`
  - [x] **Live test 1 (natural):** agv-2 restricted to `[green]` bid only on pkg-2 (AGV-side early gate)
  - [x] **Live test 2 (direct injection):** bypass AGV gate, master rejects bid on red package from green-only agv-2; accepts from agv-1
  - [x] Learning doc `phase-3.md`
- [x] **Phase 4 (M4)** — `enforce_sequence` flag ✅ verified live
  - [x] TaskSpec gains `enforce_sequence: bool = False`
  - [x] `validate()` auto-detects from prompt: "in order", "sequentially", "sort by", "rainbow order", "one at a time", "in sequence", "one after another", "follow the order"
  - [x] `central_brain.make_plan` carries it from spec into plan dict
  - [x] `MasterAnnouncer.announce_plan` publishes first; queues rest in `_sequence_queue`
  - [x] `MasterAnnouncer.on_arm_ready` subscribes; releases next on `action=loaded`
  - [x] `_last_loaded_task_id` deduplicates duplicate `arm_ready` events
  - [x] **Live test:** prompt with "in order ... sequentially" → pkg-2 announce fires SAME MILLISECOND as pkg-1 arm_ready LOADED; pkg-3 same. Strict red→green→blue order.
  - [x] Learning doc `phase-4.md`
- [x] **Phase 5 (C4 backend)** — `GET /capabilities` ✅ verified live
  - [x] Endpoint returns full table + coordination_mode + bid_weights + defaults
  - [x] HTTP 500 + `{error, agvs:{}}` on lookup failure (honest UI error path)
  - [x] `docker/nginx.conf` regex extended to proxy `/capabilities`
  - [x] **Live test:** `curl /capabilities` through nginx returns the full table
  - [x] **DEFERRED:** Inspector display (frontend) + the Taiga "one frontend element wired" acceptance line. Tracked in `UI-INTEGRATION-DEFERRED.md` and called out in `coordination-us34-evidence.md`.
  - [x] Learning doc `phase-5.md`
- [x] **Phase 6 (S1)** — A\* dynamic routing ✅ verified live
  - [x] `app/models/grid_state.py` gains blocker registry (`add_blocker`, `remove_blocker`, `clear_blockers`, `is_blocked`, `blocked_cells`); snapshot exposes them
  - [x] `/grid/obstacles` GET/POST/DELETE route for operator/test fixtures
  - [x] `scripts/agv_brain.py:_astar_path` — 4-neighbour A\* with Manhattan heuristic, blockers as hard obstacles, other-AGV cells as soft obstacles
  - [x] `_smart_path` wraps A\*; falls back to `_straight_path` on no-path or any exception
  - [x] `_fetch_route_ctx` uses `_smart_path` for route_to_input, route_to_bay (under smart mode); lane stays config-driven
  - [x] `nginx.conf` already proxies `/grid` paths via existing regex
  - [x] **Live test:** block `[5,3]`, smart-mode route detours via `[6,2]→[5,2]→[4,2]→[3,2]→[2,2]`; under `decentralized` same route walks through `[5,3]` (US 32 fallback)
  - [x] Learning doc `phase-6.md`
- [x] Final consolidation ✅
  - [x] `docs/coordination-us34-evidence.md` — live transcripts per acceptance criterion
  - [x] `docs/Work flow(12 June 2026).md` — workflow doc covering the smart path
  - [x] Acceptance checklist walked: 8/9 met, 1 frontend-wiring item explicitly deferred and tracked

---

## Out-of-scope items tracked here (do not pursue this run)

- Frontend integration (Inspector display, bus-panel extensions, route polyline, package handoff anim, BPMN cleanup). See `UI-INTEGRATION-DEFERRED.md`.
- Inheritance unification (`VehicleController` + containerized AGV brain). Schema extraction satisfies the US 34 architectural decision; full refactor is a separate US.
- LLM-authored bus dialogue (US 35).
- CI/CD changes.
- `acquire_input_station` blocking timing.
- Any backend route or service not on the listed phase list.

---

## Blockers / open items (append as they happen)

_None yet._

---

## Live-test notes (append at the end of each phase)

_(Each phase's verified curl/transcript output goes here when phase passes.)_
