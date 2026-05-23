# Phase 4 Stage 5 Committed Sub-Spec — Distributed Recovery

Handle actor crashes, reconnects, stale execution under jitter, replay catch-up,
dead-agent detection. Recovery is protocol-driven; no coordinator.

## Decisions (approved)

- Dead-agent detection: heartbeat (`AVAILABILITY_UPDATE`) + Redis lock TTLs.
- Bounded retry on bus operations; failure surfaces honestly to caller (no infinite freeze).

## Scope (5 concrete items)

### Item 1 — PEL replay on consumer reconnect (Stage 1 finding #1)

`RedisStreamsBus._consumer_loop`: on entry, BEFORE entering the `>` loop, first drain the
Pending Entries List with `XREADGROUP GROUP <consumer_id> c1 COUNT 64 STREAMS
interact:negotiation 0` until empty (loop while results non-empty). Process + ACK each
PEL entry through the same `_dispatch_entry` path. Then switch to `>` for live delivery.

This handles backend / actor crash mid-processing: on restart, in-flight unacked messages
re-deliver before the new live stream begins. Dedup LRU already prevents accidental
double-processing if the entry was processed but the ACK didn't land.

### Item 2 — Bounded retry on publish/history (Stage 1 findings #2 + #3)

`RedisStreamsBus.publish` + `history`: replace `while True: try ... sleep` with bounded
attempts. Default `_MAX_RETRIES = 30` (≈ 30s with current backoff curve). After exhaustion:
- `publish`: raise `BusUnavailable` (new exception in `app/agents/bus.py`). Caller (actor
  thread) catches and logs; the lost publish is acceptable — actor's next deadline tick
  re-evaluates from history and re-publishes if its bid is missing.
- `history`: same — raise `BusUnavailable`. Caller falls back to empty `[]` for that tick;
  the next deadline tick retries naturally.

Add a small helper in `app/agents/actor_agent.py`: wrap publish/history calls in try/except
for `BusUnavailable`; log and continue rather than crash the agent event loop.

### Item 3 — Bootstrap from history on actor start

`ActorAgent.start()`: after `subscribe_all` (which creates the consumer group), BEFORE
spawning the event loop thread, call new `_bootstrap_from_history()`. This method:
- Reads `self.bus.history()` (now includes the consumer's full visible history).
- Finds every `TASK_ANNOUNCE` (group by `task_id`; keep highest `negotiation_round` per task).
- For each such task: get-or-create `TaskParticipant`. Since participant `__post_init__`
  calls `_rehydrate_executed_legs(history)`, already-completed legs are skipped naturally.
- Also rehydrate `my_slots` from the latest `accepted_actors(history, task_id, round)`
  filtered to `self.actor_id` — so a restarted actor remembers it holds a slot.

A restarted actor with no in-flight tasks gets an empty `_tasks` dict (harmless).

### Item 4 — Heartbeat (`AVAILABILITY_UPDATE`)

`MessageType.AVAILABILITY_UPDATE` already exists in the enum (unused). Wire it:
- `ActorAgent.start()`: spawn a daemon `_heartbeat_thread` that, every 5 s, publishes:
  ```python
  Message(
      type=AVAILABILITY_UPDATE,
      task_id="",  # heartbeat is actor-scoped, not task-scoped
      negotiation_round=0,
      sender=self.actor_id,
      payload={"ts": time.time(), "state": self.state.value},
  )
  ```
- `ActorAgent.stop()`: signal the heartbeat thread to stop + join it.
- The heartbeat thread MUST handle `BusUnavailable` (drop the tick + sleep + try next interval).
- Heartbeats are observational only — no actor changes consensus decisions based on them.
- For dead-agent inference (useful for Stage 6 observability + future audit): a peer can
  compute `last_seen[actor_id] = max(ts for AVAILABILITY_UPDATE where sender == actor_id)`
  from `bus.history()`. Threshold: stale > 15 s (3 × interval) = "presumed dead". This is
  NOT wired into any consensus decision in Phase 4.

### Item 5 — Tests

#### `tests/test_pel_replay.py` (new)
- `test_pel_drains_on_consumer_restart` — Publish N messages to a stream; create consumer
  group; XREADGROUP `>` once (entries delivered to PEL but NOT acked); recreate the
  `RedisStreamsBus` instance (simulated restart, same consumer group name); start consumer;
  assert the prior unacked entries are delivered before any new entries. Use fakeredis.

#### `tests/test_bus_bounded_retry.py` (new)
- `test_publish_raises_bus_unavailable_after_max_retries` — Monkeypatch `redis.Redis.xadd`
  to always raise `RedisError`; reduce `_MAX_RETRIES` to 3 + tiny backoff; assert
  `publish()` raises `BusUnavailable` after 3 tries; total time < 1 s.
- `test_history_raises_bus_unavailable_after_max_retries` — same for `xrange`.
- `test_actor_loop_survives_bus_unavailable` — Stub `bus.publish` to raise
  `BusUnavailable`; agent's event loop logs + continues (no crash, no thread death).

#### `tests/test_bootstrap_from_history.py` (new)
- `test_actor_restart_rehydrates_active_task` — Publish `TASK_ANNOUNCE` + a BID + an ACCEPT
  for `(actor_id=arm-1, slot=pickup)` to the bus; instantiate a fresh `ArmAgent`; call
  `start()`; assert `arm-1._tasks[task_id]` exists with `my_slots == ["pickup"]` and
  appropriate `executed_legs` derivation from any prior LEG_COMPLETED.
- `test_actor_restart_skips_already_completed_legs` — Pre-seed history with a
  `LEG_COMPLETED` for `(arm-1, task-X, pickup)`; restart `arm-1`; on next consensus check,
  `pickup` is NOT re-executed.
- `test_actor_fresh_start_no_active_tasks_empty_tasks_dict` — Empty bus; fresh actor;
  `_tasks == {}`.

#### `tests/test_heartbeat.py` (new)
- `test_heartbeat_emitted_at_interval` — Start an actor with `heartbeat_interval=0.2`;
  wait 1 s; assert ≥ 4 `AVAILABILITY_UPDATE` messages from `sender=actor_id` in
  `bus.history()`.
- `test_heartbeat_includes_state_and_ts` — Assert payload has `ts` (float) and `state`
  (string from `AgentState`).
- `test_heartbeat_stops_on_agent_stop` — Stop actor; record history length; wait 1 s;
  assert no new heartbeats appear.
- `test_dead_agent_inference_from_stale_heartbeat` — Publish two
  `AVAILABILITY_UPDATE`s 20s apart for actor-X (timestamps in payload, no real sleep —
  fake the ts); helper function `last_seen(history, actor_id)` returns the latest ts;
  helper `is_presumed_dead(history, actor_id, threshold_s=15)` returns True when stale.

## Hard invariants

- Recovery is protocol-driven. No coordinator decides "X is dead" — peers observe via
  bus + locks expire via TTL + barriers re-derive validity from history.
- Heartbeats are observational only. NEVER consulted for consensus decisions.
- PEL replay is for at-least-once correctness, not authority.
- `BusUnavailable` surfaces honestly — actors log + continue, never freeze.
- Bootstrap-from-history uses ONLY existing pure helpers (`accepted_actors`,
  `_rehydrate_executed_legs`); no new decision logic.
- Faulted-then-restarted actor remains FAULTED across restart (state persisted? — NO, in
  Phase 4 actor state restart resets to IDLE; the prior FAULT is in bus history and peers
  observed it; a restarted actor is a new actor for that task slot. Document this
  explicitly).

## Verify

- `python -m pytest tests/ -q` baseline 476 pass / 5 pre-existing fail. After: ≥ 476 + new
  pass / 5 fail. Zero NEW failures.
- 3× flake check on the new tests.
- `python -c "from app import create_app; create_app()"` constructs.

## Opus review gate

- PEL drain happens BEFORE `>` switch; no message processed twice via dedup LRU.
- Bounded retries do not freeze the agent loop; `BusUnavailable` propagates cleanly.
- Bootstrap rehydrates correctly: no over-eager `my_slots` (only this actor's accepts);
  no false executions (`executed_legs` rehydrated from `LEG_COMPLETED`).
- Heartbeats never influence consensus; the stale-detection helpers are pure functions
  over `bus.history()` with no side effects.
- Restarted actor with prior LEG_COMPLETED does NOT re-execute that leg.
