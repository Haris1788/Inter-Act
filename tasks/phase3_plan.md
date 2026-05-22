# Phase 3 — Staged Implementation Plan

**Branch:** `dev-sneh` · **Spec:** `PHASE_3_PROMPT.md` · **Audit basis:** `ARCHITECTURE_REALITY_AUDIT.md`
**Mode:** plan-only until approved · subagent-driven execution (Opus orchestrates + reviews, Sonnet implements, Haiku cleanup)

## Goal

Architectural cutover. NEW decentralized runtime (`app/agents/`) already exists beside the OLD
centralized runtime (`central_brain.py`, `plan_executor.py`, `message_bus.py`). Phase 3 deletes
the old, wires the new into the live request path, replaces simulated execution with real I/O,
hardens the execution barrier, and makes the frontend an honest observer.

## Current-state facts (verified by source read)

- `ActorAgent` (`app/agents/actor_agent.py`) — daemon-thread event loop, leaderless evaluate /
  consensus / reopen all working. `_execute_leg` is a Stage-2 logging stub.
- `NegotiationBus` (`app/agents/bus.py`) — typed pub/sub, mirrors every `Message` to SocketIO as
  `bus_message`. `RoundStatus` enum has `FAILED`/`SUPERSEDED` already (`negotiation.py:32`).
- `agent_registry.start_agents()` / `stop_agents()` exist but are **never called** at startup.
- `app/__init__.py:70-80` still builds OLD `SocketIOBus` + `CentralBrain`.
- No `/negotiate` route. `/plan` (`ai_routes.py:90`) still runs the centralized planner.
- `_next_wakeup` only arms `bid_deadline_ts`; **`consensus_deadline_ts` is never used** — Case C
  (deadline failure) cannot fire.
- 11 `MessageType`s; there is **no** task-failure message type.

---

# Stage order (mandatory — do NOT reorder)

## Stage 1 — Task 1: Case C deadline failure  [single track, consensus logic — NO parallel]

**Files:** `app/agents/negotiation.py`, `app/agents/actor_agent.py`, tests.

Required behaviour: `announce → no viable bids → consensus deadline expires → failed observed →
task marked not completable`. Failure stays observational, decentralized, non-authoritative — no
coordinator owns it.

Design:
1. Add `MessageType.TASK_FAILED = "task_failed"` (12th type) — an observational marker, parallel
   to `CONSENSUS_REACHED`. **Decision flagged below.**
2. `ActorAgent`: add `_failure_announced: bool` (reset in `_on_task_announce` like
   `_consensus_announced`).
3. `_next_wakeup`: also arm `consensus_deadline_ts` while neither consensus nor failure announced.
4. New `_check_failure()` called after `_check_consensus()` in `_check_deadlines()`:
   - fire only when `now >= consensus_deadline_ts`, slots NOT complete, consensus not announced,
     this round not superseded, and no reopen for `_round+1` happened.
   - publish `TASK_FAILED` once (idempotent via `_failure_announced`), payload
     `{"round": _round, "reason": "no viable bids"/"consensus deadline expired"}`.
   - leave a bus-visible + (via SocketIO mirror) frontend-visible failure state.
5. Distinguish unfillable (no bid for a slot → FAILED) from a lost-holder slot (→ reopen, never
   FAILED). Reuse the existing `_maybe_reopen` distinction.

**INVARIANT (load-bearing):** `TASK_FAILED` is purely observational, exactly like
`CONSENSUS_REACHED`. No actor transitions another actor into a failure state. Each agent derives
failure independently from bus-visible conditions (deadline + incomplete slots), never from an
authority message. This preserves the leaderless invariant.

Verify: extend `test_negotiation_integration.py` — `test_unfillable_task_*` must now also assert a
`TASK_FAILED` message appears and no `CONSENSUS_REACHED`. Add a converged-task test asserting
`TASK_FAILED` does NOT appear.

**Opus review gate:** invariant check — no false FAILED on a slow-but-valid converge, no FAILED
race with a concurrent reopen.

## Stage 2 — Task 2 + Task 3: wire `/negotiate` + runtime startup  [simple wiring]

**Task 2 — `/negotiate`** (`app/routes/ai_routes.py` or new `negotiate_routes.py`):
`POST /negotiate → Interpreter → TaskSpec → derive_slots(source,dest) → build TASK_ANNOUNCE
(announce_ts / bid_deadline_ts / consensus_deadline_ts) → get_bus().publish() → return 202
immediately`. Route MUST NOT assign actors, build plans, orchestrate, or wait for convergence.
*Open item:* confirm Interpreter `TaskSpec` exposes source/dest zones for `derive_slots`; a
subagent verifies `interpreter.py` / `task_spec.py` field names before coding.

**Task 3 — startup wiring** (`app/__init__.py`): call `start_agents()` after config/state
pre-register; register `stop_agents()` for clean shutdown (atexit / teardown). Bus is a lazy
singleton — no extra startup. Remove nothing yet (deletion is Stage 4). `agent_registry` stays
infrastructure only — it must not coordinate, assign, or own consensus.

Verify: app boots, agents start, `/negotiate` publishes a TASK_ANNOUNCE and agents converge.

## Stage 3 — Task 4 + 5 + 8: real `_execute_leg` + barrier + higher-round halt  [single track, coupled — NO parallel]

These three are one tightly-coupled unit (execution path + its safety checks).

**Task 4 — real I/O:** override `_execute_leg` per kind. `AGVAgent` → real AGV container commands /
movement / completion; `ArmAgent` → real arm animation (reuse existing motion), remove centralized
dispatch. *Subagent first maps the real I/O surface* (`scripts/agv_server.py`, arm animation path
in `dispatch_routes.py`/`arm_routes.py`) and reports the call contract. Principle: agent decides,
execution runtime performs motion — execution infra is not orchestration authority.

**Task 5 — execution barrier:** before each leg, re-verify: local consensus still observed; own
slot still in `accepted_actors`; no higher round exists; no superseding withdraw/fault. If invalid
→ halt immediately. Must survive reopen-after-fault, stale-accept invalidation, mid-run cancel.

**Task 8 — mid-execution higher-round halt:** `_execute_leg` runs on the agent thread and blocks
the loop; chunk real I/O into steps and between steps check for a higher round (peek `_inbox` /
`bus.history()`); on higher round → invalidate local slot, halt safely. No stale execution
continuation ever.

Verify: fault mid-execution → halt; stale accept → no execution; reopen → only new-round holder runs.

**Opus review gate:** execution-barrier verification + stale-execution proof.

## Stage 4 — Task 6 ‖ Task 7: delete centralized runtime ‖ honest BusPane  [PARALLEL]

Two independent subagents (no shared files):

**Task 6 — delete centralized runtime:** grep all callers first, then delete `central_brain.py`,
`plan_executor.py`, OLD `message_bus.py`, the `/plan` planner path, centralized assignment logic,
hidden orchestration helpers; drop the `app/__init__.py` CentralBrain/SocketIOBus block. Do NOT
rename, preserve planner authority indirectly, or add fallback coordinators. Decide fate of
`scripts/agv_brain.py` (centralized tactical brain) — delete unless Task 4 still needs `agv_server.py`.

**Task 7 — honest BusPane:** frontend renders only real typed `bus_message` SocketIO traffic
(rounds, bids, accepts, withdraws, failures, reopens, convergence). Remove regex `classifyBus`,
fake negotiation streams, inferred orchestration state, simulated coordination visuals, mock
fixtures. Frontend = runtime observer, not orchestration simulator.

## Stage 5 — Task 9: full runtime integration tests  [single track]

Cover `/negotiate`, startup/shutdown, AGV + arm execution, failure/reopen, stale rounds, duplicate
reopen, stale-execution halt, deadline failure, no-valid-bid tasks. No mocked orchestration shortcuts.

## Stage 6 — Task 10: graph + audit refresh  [can run after Stage 5]

`graphify update .`, refresh graph report + runtime-ownership docs, update `CLAUDE.md`/`HANDOFF.md`.
Create `PHASE_1_IMPLEMENTATION_AUDIT.md` — honest account: what centralized authority was removed,
how decentralized negotiation + emergent consensus work, what stays centralized intentionally,
what is still transitional. Do not exaggerate decentralization.

---

# Decisions needing your sign-off

1. **New `MessageType.TASK_FAILED`** (Stage 1) — the protocol has no failure message. Adding a 12th
   observational type is the clean fit (mirrors `CONSENSUS_REACHED`). Alternative: overload an
   existing type — not recommended. **Proposed: add `TASK_FAILED`.**
2. **`/negotiate` location** — new `negotiate_routes.py` blueprint vs adding to `ai_routes.py`.
   **Proposed: new blueprint** (clean separation from the soon-deleted `/plan`).
3. **`scripts/agv_brain.py` fate** — centralized tactical brain. **Proposed: delete** once Task 4
   wires agent-driven AGV I/O; keep `agv_server.py` only if it is the real AGV container API.

# Deferred to Phase 2 (must NOT implement)

distributed processes, networked agents, P2P messaging, concurrent negotiations, semantic
counter-offers, advanced pathfinding, adaptive AI bidding, emergent optimization.

# Parallelization summary

True multi-agent parallel execution: whenever N tasks are genuinely independent, deploy N
implementation/review agents concurrently — no sequential handoffs. Bounded only by the spec's
mandatory order and the explicit no-parallel zones (consensus / round semantics / barriers /
reopen).

- NO parallel (shared runtime invariants): Stage 1 (consensus), Stage 3 (barrier/reopen/execution).
- PARALLEL: Stage 4 (Task 6 ‖ Task 7) — 2 concurrent agents. Stage 3's I/O-surface *mapping* (arm
  ‖ AGV) and Stage 1/3 review passes may also run concurrently.
- Sequential single-track by dependency: Stages 2, 5, 6.
