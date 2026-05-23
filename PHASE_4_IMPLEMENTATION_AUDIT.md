# INTER.act — Phase 4 Implementation Audit

**Date:** 2026-05-23
**Branch:** `dev-sneh`
**Scope:** distributed runtime realism + multi-task decentralized coordination

This audit follows `PHASE_1_IMPLEMENTATION_AUDIT.md` (post-Phase 3 decentralization audit).
Honest tone — what shipped, what is intentionally bounded, what is still transitional.

---

## 0. Executive verdict

INTER.act crossed from **single-process decentralized** to a **real distributed
coordination runtime**. The bus is a typed pub/sub over Redis Streams. Shared state
(locks, agv_state, arm_state, handshake signals) lives in Redis. Actors run as
per-process services subscribed independently. Multiple negotiations run concurrently
without state leakage. Execution transitions persist on the same log as the protocol.
Crashes are recovered by protocol + lock TTLs, never by a coordinator. The frontend
inspects the runtime; it does not orchestrate it. 520 tests pass / 5 pre-existing
failures (unchanged from Phase 3 baseline). All distributed primitives have
parametrized tests across both backends (in-memory + Redis).

What did NOT change: the leaderless invariant. Every agent still calls `public_winner`
on its own `bus.history()` and computes the same winner without any coordinator. The
transport changed; the rule did not.

---

## 1. Stage-by-stage delivery

| Stage | Deliverable | Files (representative) | Tests added |
|---|---|---|---|
| 1 | Distributed bus — Redis Streams transport, in-process/redis interface, SocketIO mirror consumer | `app/agents/bus.py` (interface), `app/agents/redis_bus.py`, `app/services/bus_mirror.py` | +39 |
| 2-A | Shared state to Redis — `RedisLock` (`SET NX PX` + CAS via WATCH/MULTI), `LockKeepalive`, dual-backend `grid_state`/`agv_state`/`arm_state`/handshake signals | `app/models/redis_lock.py`, `app/models/{grid,agv,arm}_state.py`, `app/routes/agv_routes.py` | +147 |
| 2-B | Per-actor process entrypoint | `scripts/actor_runner.py`, `Dockerfile.actor` | +10 |
| 2-C | Container reconciler extension (arms + AGVs label-owned via `interact.role=actor`); backend startup removes in-process `start_agents()` | `app/services/container_manager.py`, `app/__init__.py`, `docker-compose.yml` | +14 |
| 2-D | Multi-actor isolation test against fakeredis-backed bus + state | `tests/distributed/test_actor_isolation.py` | +7 |
| 3 | Concurrent negotiations via `TaskParticipant` encapsulation | `app/agents/task_participant.py`, `app/agents/actor_agent.py`, per-task `_signals` rekey | +5 |
| 4 | Persistent execution log — new `MessageType`s `LEG_STARTED`/`LEG_COMPLETED`/`LEG_HALTED`; `executed_legs` derived from `bus.history()` | `app/agents/negotiation.py`, `app/agents/actor_agent.py`, `app/agents/task_participant.py` | +6 |
| 5 | Distributed recovery — PEL drain on consumer reconnect, bounded retry → `BusUnavailable`, `_bootstrap_from_history()`, heartbeat thread + presumed-dead helpers | `app/agents/redis_bus.py`, `app/agents/actor_agent.py`, `app/agents/heartbeat.py` | +12 |
| 6 | Replay + observability — `/bus/replay`, `/bus/actors`; frontend Replay panel + Timeline + Fault history + Actor inspector; observability docs | `app/routes/replay_routes.py`, `frontend/src/chrome/Replay/*`, `frontend/src/chrome/Observability/*`, `docs/observability.md` | +25 (backend; frontend typecheck/build clean) |
| 7 | Distributed chaos integration tests | `tests/distributed/chaos_harness.py`, `tests/distributed/test_chaos.py` | +7 |

**Total: +272 tests, 0 regressions, 5 pre-existing failures untouched (488 of those 520 are
Phase 4 additions to a 248 Phase 3 baseline).**

---

## 2. How the runtime works now

1. `POST /negotiate` → interpreter → `derive_slots(source, dest)` → publish
   `TASK_ANNOUNCE` to the bus → return 202. Unchanged from Phase 3.
2. Each actor process subscribes to its own consumer group on `interact:negotiation`.
   `_bootstrap_from_history()` rehydrates any active tasks the actor is a participant
   in (including derived `executed_legs` from prior `LEG_COMPLETED`s). Heartbeat
   thread starts emitting `AVAILABILITY_UPDATE` every 5 s.
3. Per-task lifecycle: `TaskParticipant.on_announce` → capability + bid → evaluate at
   `bid_deadline_ts` → publish ACCEPT/WITHDRAW → observe consensus or failure → on
   winning slot, execute leg (with `LEG_STARTED`/`LEG_COMPLETED`/`LEG_HALTED` book-ends).
4. Locks: Redis SET NX PX with 180 s default TTL + `LockKeepalive` renewing every 60 s
   during held windows. On actor crash, TTL expires → next actor can acquire. CAS
   release (compare-and-delete) prevents double-release races.
5. Faults: published as `FAULT`/`WITHDRAW` on the bus. Other agents observe and reopen
   the round (`maybe_reopen` byte-identical idempotent). Faulted-then-restarted actor
   re-bootstraps as IDLE — its previous FAULT is in the bus log but the new process
   does not inherit FAULTED state (Phase 4 limitation; cross-restart faulted-state
   persistence is Phase 5).
6. Observability: frontend Replay panel scrubs the log, Timeline plots per-task lanes,
   FaultHistory groups fault types per task, ActorInspector reports per-actor heartbeat
   freshness + `presumed_dead`. All read-only.

---

## 3. What is intentionally bounded (Phase 4 limits)

- **One Redis instance.** Single point of write coordination. Multi-region / clustered
  Redis = Phase 5+.
- **Restarted actor begins IDLE.** Prior FAULT remains in bus log; the new process does
  not inherit FAULTED state. Documented in `_bootstrap_from_history`.
- **Concurrent-task capacity not strictly enforced.** Agents can bid on multiple tasks;
  serial execution via the single-threaded event loop is the de facto cap.
- **Lock TTL = 180 s.** A leg whose load/unload handshake exceeds 120 s + buffer would
  exceed TTL. `LockKeepalive` mitigates by renewing every 60 s during held windows.
- **Chaos tests are in-process + fakeredis.** Real subprocess + toxiproxy + iptables
  chaos lives in a Linux CI environment; deferred to Phase 5. The protocol invariants
  are verified, the OS-level partition modes are not.
- **HTTP routes for handshake signals are deprecated** (no live caller post-Phase 3).
  Kept with deprecation docstrings; not yet deleted. Pass `task_id=""` internally.
- **`from`/`to` slicing on `/bus/replay`** is index-based for both bus backends. XID
  range slicing on the Redis bus is a future enhancement.
- **5 pre-existing test failures** (`test_arm_execution` ×1, `test_warehouse_config`
  ×4) remain — predate Phase 3, untouched.

---

## 4. Decentralization invariants preserved

- Pure-functional consensus over `bus.history()`. Every actor computes the same winner
  from the same observed history.
- No coordinator. Recovery is bus-driven + TTL-driven. No daemon owns liveness or
  authority.
- Bus is dumb: delivers, records. Makes no decisions.
- `CONSENSUS_REACHED`, `TASK_FAILED`, `LEG_*`, `AVAILABILITY_UPDATE` are observational.
  Agents act on their own state checks, never on another actor's observational message.
- Faults are agent-level (actor cannot continue); reopens are per-task (other actors
  reform consensus).
- `TaskParticipant` encapsulates per-task state — replay/recovery/checkpoints/halt
  flags scoped per task. Cross-task state leakage is structurally prevented.

---

## 5. Frontend

- BusPane renders 15 typed `bus_message` events with chip/tone mapping
  (`busMessageChip.ts`) — single source of truth.
- Replay panel + Timeline render the same log with playback control and per-`(task_id,
  round)` lane visualization.
- Fault history groups faults per task. Actor inspector reports heartbeat freshness +
  presumed-dead. Neither offers a "kill", "restart", or "force fault" action.
- GoalBar still POSTs `/negotiate` (Phase 3). Empty mock-mode panels stay honest.

---

## 6. Deferred to Phase 5

- Adaptive AI bidding, semantic counter-offers, RL, autonomous manipulation, computer
  vision (per Phase 4 spec).
- Multi-region / clustered Redis.
- Real subprocess + OS-level chaos in CI (toxiproxy, iptables, kill -9 across
  containers).
- Cross-restart FAULTED state persistence.
- Per-actor concurrency cap enforcement via `capacity`.
- XID range slicing on `/bus/replay`.
- Delete the deprecated handshake HTTP routes.

---

## 7. Evidence index

| Claim | File |
|---|---|
| Redis Streams bus | `app/agents/redis_bus.py` |
| Bus interface + InProcessBus + selector | `app/agents/bus.py` |
| SocketIO mirror consumer | `app/services/bus_mirror.py` |
| Redis lock (CAS via WATCH/MULTI) + Keepalive | `app/models/redis_lock.py` |
| Dual-backend state | `app/models/{grid,agv,arm}_state.py` |
| Per-actor entrypoint | `scripts/actor_runner.py`, `Dockerfile.actor` |
| Reconciler extension | `app/services/container_manager.py` |
| `TaskParticipant` | `app/agents/task_participant.py` |
| LEG_* MessageTypes | `app/agents/negotiation.py` |
| Bootstrap from history + heartbeat + helpers | `app/agents/actor_agent.py`, `app/agents/heartbeat.py` |
| PEL drain + bounded retry | `app/agents/redis_bus.py` |
| Replay + inspector endpoints | `app/routes/replay_routes.py` |
| Frontend Replay panel + Timeline | `frontend/src/chrome/Replay/` |
| Frontend Fault history + Actor inspector | `frontend/src/chrome/Observability/` |
| Observability docs | `docs/observability.md` |
| Chaos harness + 7 scenarios | `tests/distributed/{chaos_harness,test_chaos}.py` |

---

*End — Phase 4 cutover complete. INTER.act is a real distributed coordination runtime.*
