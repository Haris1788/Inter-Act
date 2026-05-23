# Phase 4 Stage 4 Committed Sub-Spec — Persistent Runtime State

The Redis Streams bus (Stage 1) already persists rounds, bids, accepts, withdraws, faults,
consensus, failures. Stage 4 closes the gap: persist execution transitions on the same bus
(no parallel store) and make state derivable from the log alone — the foundation for replay,
recovery, and audit.

## Decision (approved — plan #5)

Add execution-transition `MessageType`s on the existing bus. Single source of truth =
`bus.history()`. No separate execution stream, no parallel store.

## Required behaviour

- Three new `MessageType`s: `LEG_STARTED`, `LEG_COMPLETED`, `LEG_HALTED`. All observational
  (like `CONSENSUS_REACHED` and `TASK_FAILED`) — actor publishes about its own leg, no
  authority claim, no other actor reads them as instructions.
- Emit sites in `ActorAgent._check_consensus_for` and `_execute_leg` wrap:
  - Before calling `_execute_leg(slot_id)`: publish `LEG_STARTED` with payload
    `{"slot_id": ..., "task_id": ..., "round": ...}`.
  - On `_execute_leg(slot_id)` returning `True`: publish `LEG_COMPLETED` with same payload.
  - On `_execute_leg(slot_id)` returning `False`: publish `LEG_HALTED` with same payload.
- `TaskParticipant.executed_legs` becomes a derived field. On `on_announce`/participant
  creation, rebuild from `bus.history()`: every `LEG_COMPLETED` where `sender == actor_id`
  and `task_id == self.task_id` adds its `slot_id`. Cross-restart dedup is automatic and
  free — no separate Redis Hash.
- `_check_consensus_for` continues to skip slots already in `executed_legs`. With log-derived
  state, an actor restarted after a crash sees its prior `LEG_COMPLETED`s on rehydration and
  does not re-execute completed legs.

## Files

### 1. `app/agents/negotiation.py`
- Add three new values to `MessageType`: `LEG_STARTED = "leg_started"`, `LEG_COMPLETED =
  "leg_completed"`, `LEG_HALTED = "leg_halted"`. Update enum docstring count (12 → 15).
- Update the `test_twelve_message_types_exist` test fixture / value-set test in
  `tests/test_negotiation.py` to expect the new total + names.

### 2. `app/agents/actor_agent.py`
- `_check_consensus_for(participant)`: around the `_execute_leg` call:
  ```python
  self._publish_leg_event(MessageType.LEG_STARTED, slot_id, participant)
  completed = self._execute_leg(slot_id)
  evt = MessageType.LEG_COMPLETED if completed else MessageType.LEG_HALTED
  self._publish_leg_event(evt, slot_id, participant)
  ```
- New helper `_publish_leg_event(msg_type, slot_id, participant)` that constructs a Message
  with `task_id=participant.task_id`, `negotiation_round=participant.round`,
  `sender=self.actor_id`, `slot_id=slot_id`, `payload={"slot_id": slot_id, "task_id":
  participant.task_id, "round": participant.round}`, and publishes via `self.bus.publish`.
- `_current_participant` setting unchanged.

### 3. `app/agents/task_participant.py`
- On `__init__` AND `on_reopen_announce` (whatever creates/updates the participant for a
  task): call a new method `_rehydrate_executed_legs(history)` that scans
  `bus.history()` (or accepts pre-fetched history) and populates `executed_legs` from
  `LEG_COMPLETED` messages matching `(sender=actor_id, task_id=self.task_id)`. Idempotent.
- Add `_rehydrate_executed_legs(self, history: list[Message]) -> None` as a public method
  for explicit re-derivation use (Stage 5 reconnect/replay path).
- Document: `executed_legs` is now a derived view of the bus log; it is mutated ONLY by
  `_rehydrate_executed_legs` and by `_check_consensus_for` after a `LEG_COMPLETED` is
  published (so the in-process append matches what rehydration would compute).

### 4. Tests

#### `tests/test_negotiation.py`
- Update the message-type-set test for the 3 new values.

#### `tests/test_leg_events.py` (new)
- `test_leg_started_completed_emitted_on_full_leg` — single converged task → exactly one
  LEG_STARTED then one LEG_COMPLETED per held slot, in order, sender = actor_id.
- `test_leg_halted_emitted_on_halt` — force a halt mid-leg (e.g. higher-round announce
  while executing OR stub `_execute_leg` to return False) → exactly one LEG_STARTED then
  one LEG_HALTED; no LEG_COMPLETED.
- `test_executed_legs_rebuilt_from_history` — create a fresh `TaskParticipant` with a bus
  pre-seeded with a LEG_COMPLETED message for `(sender=actor_id, task_id)`; participant's
  `executed_legs` includes that slot WITHOUT replaying real execution.
- `test_no_double_execution_after_rehydration` — actor "restarts" (new ActorAgent instance,
  same bus history); on next consensus check, the prior LEG_COMPLETED slot is skipped (no
  duplicate execute).
- `test_leg_events_are_observational` — every LEG_* message has the publishing actor's id
  as `sender`, never another actor's id. Pure observation, never authority.

#### `tests/test_replay_determinism.py` (new)
- `test_state_function_of_history` — given a recorded `bus.history()` log of a converged
  scenario, deriving `executed_legs`/`my_slots`/`consensus_announced` via `accepted_actors`
  + `LEG_COMPLETED` scan produces the same state regardless of how many times the
  derivation runs (idempotent, deterministic).

## Hard invariants

- LEG_* messages are observational. No actor changes its decisions based on another actor's
  LEG_* — they are bus-visible audit trail + frontend observation + self-recovery only.
- `executed_legs` is a derived view of `bus.history()` — single source of truth.
- Pure-functional consensus over `bus.history()` remains the rule.
- No new MessageType is decision-bearing.
- No separate persistence store. Bus IS the persistence.
- Backward compat: existing tests' single-task path keeps passing; new LEG_* messages just
  appear in `bus.history()` and tests that count messages must be updated to expect them.

## Verify

- `python -m pytest tests/ -q` baseline 470 pass / 5 pre-existing fail. After: ≥ 470 + 6
  new pass / 5 fail. Zero new failures.
- Existing tests that previously asserted exact message counts in `bus.history()` may need
  to be updated to account for LEG_* additions. Treat any failure caused by message-count
  asserts as expected work (update the asserts to include the new event types) — do NOT
  suppress or remove the new emit sites.
- `python -c "from app import create_app; create_app()"` constructs in both modes.

## Opus review gate

- Replay determinism: replaying `bus.history()` from xid 0 produces the same
  `executed_legs` / `accepted_actors` view as the original run.
- LEG_* never influence consensus or barrier logic — purely observational.
- No race between `_publish_leg_event(LEG_COMPLETED)` and the next deadline tick (the
  publish is synchronous before `executed_legs.append`, ensuring history reflects the
  completion before any other agent's `bus.history()` read).
