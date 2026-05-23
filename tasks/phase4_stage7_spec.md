# Phase 4 Stage 7 Committed Sub-Spec — Distributed Integration Testing

"The most important test stage so far" (Phase 4 spec). Stress the runtime under distributed
conditions; assert every invariant explicitly; no false greens.

## Decision (approved)

Chaos tooling: toxiproxy + raw kill-9 was approved as the ideal. But the user is on Windows
and toxiproxy / iptables-based partition tooling is Linux-first. **Pragmatic scoping:**
chaos simulated at the Python level (kill threads, drop messages, inject Redis errors)
against fakeredis with multi-thread actors. Honest about the limitation: this exercises
protocol invariants but not real subprocess crash + real OS-level network partition. Real
subprocess + toxiproxy chaos requires a Linux CI environment and is documented as a
Phase 5 enhancement; the test harness should be structured so swapping the chaos layer to
real subprocess + toxiproxy is mechanical.

## What to build

### Test infrastructure — `tests/distributed/chaos_harness.py` (new)
Helpers reusable across all 7 scenarios:

- `make_chaos_env(n_arms=2, n_agvs=2)` — context manager that wires
  `INTERACT_BUS=redis` + `INTERACT_STATE=redis` against a fresh `fakeredis.FakeServer()`
  shared by every module (`agv_state`, `arm_state`, `grid_state`, `signals`,
  `RedisStreamsBus`). Spawns N ArmAgents + M AGVAgents as threads (same pattern as
  Stage 2 Track D's `_redis_env`). Returns `{bus, agents, server, client}`.
- `kill_actor(agent)` — abrupt thread death simulation: `agent._running = False`,
  release the agent's daemon thread without graceful shutdown; locks held by this actor
  are NOT released (testing TTL-based recovery).
- `inject_dup_delivery(bus, n=2)` — wraps the bus client so each XADD also re-publishes
  the same entry `n` times → consumers see duplicates → dedup LRU must filter.
- `inject_stale_message(bus, sender, task_id, round)` — XADD a `Message` with an OLDER
  `negotiation_round` value to verify monotonic-round filtering ignores it.
- `partition_actor(actor_id, server, client)` — disconnects this actor's Redis client so
  publish + history calls raise; after `with` exit, reconnects.
- `clock_skew(actor, delta_s)` — monkeypatches `time.time` for that actor's thread to
  return now+delta (positive = clock ahead, negative = clock behind).

If `fakeredis` lacks any required behaviour (XAUTOCLAIM, advanced eviction), document
the workaround inline in the helper.

### Scenarios — `tests/distributed/test_chaos.py` (new)

Each test = one focused invariant. Use `make_chaos_env`. Each test must be deterministic
and pass 3× consecutively.

1. **`test_actor_crash_mid_consensus_reconverges`** — Publish a full
   `TASK_ANNOUNCE`. After `agv-1` accepts the transport slot but before its leg
   completes, `kill_actor(agv-1)`. Wait > TTL. Assert: a peer (or any non-faulted agent)
   publishes a reopen (round 1 announce) and a different AGV wins. Final state:
   `accepted_actors(history, task_id, 1)["transport"]` is a healthy AGV.
2. **`test_duplicate_delivery_does_not_double_process`** — `inject_dup_delivery(bus, n=3)`.
   Run a converged negotiation. Assert: `_messages_seen` on each agent increases by 1
   per unique message (dedup LRU filters duplicates).
3. **`test_reconnect_replay_after_restart`** — Publish a partial negotiation (announce
   + some bids). Tear down the bus instance; create a NEW `RedisStreamsBus` against the
   same FakeServer; spawn a fresh actor with the same `actor_id`; call
   `_bootstrap_from_history`. Assert: the new actor's `_tasks[task_id].my_slots` matches
   the slots that actor previously accepted.
4. **`test_stale_round_announce_ignored`** — Run a converged round 0. Inject a stale
   `TASK_ANNOUNCE` with `negotiation_round=0` for the same task_id with a NEW payload.
   Assert: no agent re-adopts; consensus state unchanged (still the round 0 winners).
5. **`test_concurrent_negotiations_no_signal_leakage`** — Publish task A and task B
   simultaneously. Drive both to consensus + execution stub. Assert: task A's
   `signal_load(task_a, agv)` does NOT satisfy task B's `poll_load(task_b, agv)` and
   vice versa.
6. **`test_clock_skew_per_actor_deadline_independence`** — Spawn agents; apply
   `clock_skew(agv-1, +2.0)` (clock 2 s ahead). Publish a task. Assert: agv-1 still
   honours its own `bid_deadline_ts` (uses its skewed time), and the protocol completes
   correctly (every actor uses its own clock, which is by design — leaderless rule does
   not require synchronized clocks).
7. **`test_partitioned_actor_halts_execution_safely`** — Spawn agents; converge a task;
   begin executing; `partition_actor(agv-1, ...)` mid-leg so its publish/history calls
   raise `BusUnavailable`. Assert: agv-1's event loop survives (does NOT crash),
   `_execute_leg` returns False eventually (barrier fails or fault-timeout fires), held
   locks expire by TTL. NO stale execution continues. NO rogue commit lands on Redis
   after partition.

For each, use `time.sleep(settle)` windows generous enough to clear `BID_WINDOW_S +
CONSENSUS_WINDOW_S + TTL` where relevant. Document settle choices inline.

## Hard invariants

- Every test asserts the invariant explicitly. No "if it didn't crash, pass" non-asserts.
- Deterministic. 3× flake check passes.
- Honest about scope: test docstrings + `tests/distributed/README.md` (or top-of-file
  docstring) state the in-process+fakeredis chaos scoping vs the real subprocess +
  toxiproxy chaos that belongs to a Linux CI environment (deferred to Phase 5).
- No mocked orchestration shortcuts — real ActorAgent, real bus, real Redis-backed
  state/locks.
- Test stays under 30 s wall time per scenario (TTL-based tests use SHORT TTLs).

## Verify

- `python -m pytest tests/distributed/test_chaos.py -q` — every test passes 3 runs.
- Full suite: baseline 513 pass / 5 pre-existing fail. After: ≥ 513 + 7 new pass / 5 fail.
  Zero new failures.

## Opus review gate

- Every scenario asserts an invariant explicitly.
- No false greens (test that would pass even if the invariant didn't hold).
- Honest about limitation (no real subprocess crash, no real OS-level partition).
- Chaos harness is reusable + small.
- Scope deferral is explicit (subprocess + toxiproxy = Phase 5).
