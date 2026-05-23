# Phase 4 Stage 3 Committed Sub-Spec — Concurrent Negotiations (TaskParticipant)

Support multiple simultaneous tasks per actor by encapsulating per-task state in a
`TaskParticipant` object. Single track — consensus invariants. NO parallel work.

## Decision (approved, refined from plan #4)

Lightweight `TaskParticipant` object, NOT a flat `_tasks: dict[task_id, dict]` mutation jungle.
Same leaderless rule + actor ownership + decentralization; encapsulated per-task state machine
so Phase 5's replay/recovery/checkpoint/reconnect surface stays tractable.

## Required behaviour

- An actor participating in N concurrent tasks tracks N independent participant objects. Each
  participant owns: round, accepts, executed legs, reopen state, deadlines, halt flag, replay
  cursor (xid checkpoint — Phase 5).
- Single-task scenarios degenerate to one entry in `self._tasks` — every existing test passes
  unchanged.
- Per-task signal scoping: task A's `load_ready` does not satisfy task B's `poll_load`.
- Per-task execution barrier: `participant.execution_valid(slot_id)` independent of other tasks.
- Faults are agent-level (the actor can't continue any task) — withdraw every held slot across
  every task. Reopens are per-task.
- Concurrency cap: `capacity` informs bidding eligibility (Phase 4 refinement may be partial; do
  not block on full implementation — note as Phase 5 polish if not landed).

## Files

### 1. `app/agents/task_participant.py` (new)

```python
@dataclass
class TaskParticipant:
    actor_id: str
    bus: NegotiationBus
    bid_score_fn: Callable[[dict], Optional[float]]   # actor's _bid_score
    execute_leg_fn: Callable[[str, str, int], bool]   # actor's _execute_leg(slot_id, task_id, round) -> completed?

    task_id: str
    round: int
    task: dict                                       # announce payload
    evaluated_round: int = -1
    my_slots: list[str] = field(default_factory=list)
    executed_legs: list[str] = field(default_factory=list)
    reopened_rounds: set[int] = field(default_factory=set)
    consensus_announced: bool = False
    failure_announced: bool = False
    halt_flag: bool = False
    replay_cursor: Optional[str] = None              # Phase 5 — declared now

    # Methods (logic ported from ActorAgent, all reading bus.history() only):
    def on_announce(self, message) -> None: ...
    def place_bids(self) -> None: ...
    def evaluate(self) -> None: ...
    def check_consensus(self) -> None: ...
    def check_failure(self) -> None: ...
    def maybe_reopen(self, trigger) -> None: ...
    def execution_valid(self, slot_id: str) -> bool: ...
    def next_deadline(self) -> Optional[float]: ...
```

All decision logic that today lives on `ActorAgent` as `_evaluate`/`_check_consensus`/
`_check_failure`/`_maybe_reopen`/`_execution_valid` moves into the participant, parameterized
by task. Leaderless rule (`public_winner`, `surviving_bids`, `accepted_actors`, `slots_complete`)
unchanged.

### 2. `app/agents/actor_agent.py` (refactor)

Shrink the base class to: lifecycle (thread + inbox + start/stop), bus subscribe, dispatcher,
fault, capability broadcast (agent-level, sent once per task adoption).

- New `self._tasks: dict[str, TaskParticipant] = {}` — keyed by `task_id`.
- `_handle(message)`:
  - On `TASK_ANNOUNCE`: get-or-create participant for `message.task_id` and call
    `participant.on_announce(message)`.
  - On `WITHDRAW`/`FAULT`: route to `self._tasks.get(message.task_id)` → `maybe_reopen`.
  - Other types: enqueue/observe (consensus + failure check happens on deadline tick).
  - Monotonic-round guard moves into the participant.
- `_check_deadlines()`: iterate participants, call `check_consensus` + `check_failure`.
- `_next_wakeup()`: min over `participant.next_deadline()` across all participants, capped by
  `_IDLE_TICK_S`.
- `_do_fault(reason)`: for every participant, withdraw all `participant.my_slots`; publish
  one FAULT per task; state FAULTED. Faulted agent stays out of new task adoptions.
- Remove: `_task_id`, `_round`, `_task`, `_evaluated_round`, `_my_slots`, `_executed_legs`,
  `_reopened_rounds`, `_consensus_announced`, `_failure_announced`. All move into participant.
- KEEP: `_fault_requested`, `_fault_reason` (agent-level), `state` (agent-level).
- `_execute_leg(slot_id, task_id, round)` — base stub returns True. Subclasses override.

### 3. `app/agents/arm_agent.py` (refactor)

- `_execute_leg(self, slot_id, task_id, round) -> bool` — signature update. Use `task_id`+`round`
  to read `accepted_actors(history, task_id, round)["transport"]` for the AGV id.
- Handshake calls: `signal_load(task_id, agv_id, object_id)` and `signal_unload(task_id,
  agv_id)`, `poll_unload(task_id, agv_id)`. Per-task scoping.

### 4. `app/agents/agv_agent.py` (refactor)

- `_execute_leg(self, slot_id, task_id, round) -> bool` — signature update.
- Handshake calls: `poll_load(task_id, agv_id)`, `clear_load(task_id, agv_id)`,
  `poll_unload(task_id, agv_id)`, `clear_unload(task_id, agv_id)`. Per-task scoping.
- `_execution_valid(...)` calls become `participant.execution_valid(slot_id)` — passed in or
  resolved via a back-reference. Pattern: agent threads pass `participant` into `_execute_leg`,
  or the call sites use a closure / lookup `self._tasks[task_id].execution_valid(slot_id)`.

### 5. `app/routes/agv_routes.py` (handshake rekey)

- `_signals` rekey from `{agv_id: dict}` to `{(task_id, agv_id): dict}` (in-memory backend) and
  Redis Hash key `signals:<task_id>:<agv_id>` (Redis backend).
- Promoted functions: `signal_load(task_id, agv_id, object_id)`, `poll_load(task_id, agv_id)`,
  `clear_load(task_id, agv_id)`, signal_unload trio same.
- HTTP routes: extend POST body/path to carry `task_id`. If no caller exists today (Phase 3
  cutover removed agv_brain which was the only caller), mark the HTTP routes as deprecated /
  not-required-for-Phase-4-actor-flow and consider deletion in a follow-up. Verify by grep.

### 6. Tests

- Existing `tests/test_negotiation_integration.py` + `tests/test_negotiation_integration_redis.py`
  + `tests/test_runtime_integration.py` + `tests/distributed/test_actor_isolation.py` MUST pass
  unchanged (single-task path degenerates).
- New `tests/test_concurrent_negotiations.py`:
  - `test_two_concurrent_tasks_both_reach_consensus` — announce task-A then task-B with
    different `task_id`s; both reach `CONSENSUS_REACHED`; each task's slot accepts are
    independent.
  - `test_fault_in_task_a_does_not_affect_task_b` — agent faults during task A execution; task A
    reopens (winner = different actor); task B's participant continues normally.
  - `test_per_task_signal_scoping` — `signal_load(task_a, agv-1, ...)` does NOT satisfy
    `poll_load(task_b, agv-1)`.
  - `test_per_task_executed_legs_isolation` — agent executes a leg for task A; later wins same
    slot id in task B; the task-B leg DOES execute (not blocked by task-A's `executed_legs`).
  - `test_per_task_reopen_isolation` — concurrent faults in task A and task B each trigger
    independent reopens with non-colliding round-1 announces.

## Hard invariants

- Pure-functional consensus over `bus.history()` unchanged.
- Leaderless `public_winner` rule unchanged.
- No bare `self._round` / `self._task_id` reads in `ActorAgent` after refactor — every read
  goes through a participant.
- Faulted agent stays out of new task adoptions (Phase 3 invariant carried over).
- Single-task tests pass unchanged.
- No new MessageType. No new coordinator. No central scheduler.

## Verify

- `python -m pytest tests/ -q` baseline 465 pass / 5 pre-existing fail. After refactor: at
  least 465 + N new (concurrent tests) / 5 fail. ZERO regressions in existing tests.
- Flake check: run new concurrent tests 3× consecutively; must pass each run.

## Opus review gate

- Signal scoping: no cross-task signal leakage.
- Reopen collision absence: concurrent reopens for different tasks produce independent
  round-1 announces.
- No state leakage across tasks (executed_legs, reopened_rounds, consensus_announced isolated).
- Deterministic winner selection unchanged (leaderless rule still applies per task).
- Faulted agent properly withdraws ALL held slots across ALL tasks.
