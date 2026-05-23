# Phase 4 — Staged Implementation Plan

**Branch:** `dev-sneh` · **Spec:** `PHASE_4_PROMPT.md` · **Predecessor:** Phase 3 cutover (committed)
**Mode:** plan-only until approved · multi-agent parallel execution (Opus orchestrates + reviews, Sonnet implements, Haiku cleanup). Every distributed stage gets an Opus review gate.

## Goal

Move INTER.act from single-process decentralized to **real distributed**: distributed bus,
per-process actor isolation, concurrent negotiations, persistent event history, distributed
recovery, replay/observability, distributed integration tests. Preserve the leaderless
invariant — no hidden coordinator may return.

## Approved decisions

- Bus transport: **Redis Streams**.
- Session scope: plan only, then approve before execution.

## Current-state baseline (Phase 3 final)

- `NegotiationBus` (`app/agents/bus.py`) — in-process singleton, RLock-guarded, typed pub/sub,
  history is a `list[Message]`, mirrors to SocketIO as `bus_message`. Dumb: no decisions.
- `ActorAgent` (`app/agents/actor_agent.py`) — daemon-thread event loop; single active task
  (`_task_id`, `_round`, `_my_slots`, `_executed_legs`, `_reopened_rounds`, `_failure_announced`).
- `agent_registry.start_agents()` boots agents in-backend at startup.
- Execution: AGVAgent drives `agv_state`/`grid_state` directly in-backend; ArmAgent uses
  `sweep_arm`. Load/unload handshake = `_signals: dict[agv_id, ...]` in `agv_routes.py`.
- 248 tests pass / 5 pre-existing fail. Frontend BusPane renders real `bus_message`s.

What this means for Phase 4: the bus abstraction is genuinely swap-able; consensus + barrier
logic is pure-functional over `bus.history()` (transport-agnostic); the H9 known gotcha
(`_signals` keyed by `agv_id`) becomes Stage 3's first target.

---

# Stage order

Stages 1→7 are sequential by dependency (per Phase 4 spec). Within each stage, parallelize the
parts the spec marks safe; never parallelize consensus / recovery / consistency / replay logic.

## Stage 1 — Distributed Bus (Redis Streams)  [single track for protocol; parallel for adapter+tests+manifests]

**Files:** `app/agents/bus.py` (refactor to abstraction), new `app/agents/redis_bus.py`,
`docker-compose.yml` (add Redis service), tests, deployment manifests.

Design:
- Keep `NegotiationBus` as a public **interface** (`publish` / `subscribe` / `subscribe_all` /
  `history`). Extract two implementations: `InProcessBus` (current behaviour, kept for tests) and
  `RedisStreamsBus`. `get_bus()` returns one based on env (`INTERACT_BUS=redis|inprocess`).
- Stream layout: ONE global stream `interact:negotiation` (Redis XADD). `Message.task_id` is in
  the payload, consumers filter client-side. Rationale: cheapest, ordering preserved, replay
  trivial; per-task streams complicate replay-by-round. Re-evaluate if throughput becomes a
  problem (it will not at INTER.act scale).
- IDs: XID becomes the canonical message sequence. `bus.history()` = `XRANGE - +`. Replay from
  a checkpoint = `XRANGE <last_xid> +`.
- Consumer model: each actor = its own consumer group (`group=<actor_id>`); within the group one
  consumer per process. This preserves the existing `subscribe_all` semantics (every actor sees
  every message) while giving us at-least-once + auto-claim for crash recovery.
- Idempotency: Redis can redeliver. Actors already ignore own-sender messages; add a
  `seen_xids: set` (bounded LRU) on each subscriber to drop dupes. Pure consensus is idempotent
  already (`_consensus_announced` / `_failure_announced` / `_reopened_rounds`), so dedup is
  belt-and-braces.
- Persistence: Redis AOF (`appendonly yes`, `appendfsync everysec`) — survives container restart.
- SocketIO mirror: backend keeps a single Redis subscriber that fans bus traffic to the browser
  as `bus_message` — frontend contract unchanged.
- Reconnect: subscriber stores `last_xid` in memory; on reconnect XREADGROUP resumes from
  pending-entries-list (PEL) automatically.

Parallel within stage:
- A (single track, blocking): `RedisStreamsBus` adapter + tests proving `bus.history()` /
  `publish` / `subscribe_all` semantic parity with `InProcessBus` (parametrize the same suite
  over both impls).
- B ‖ C ‖ D (concurrent after A's interface lands):
  - B — `docker-compose.yml` + `scripts/generate_compose.py` Redis service + AOF volume.
  - C — env wiring (`INTERACT_BUS`, `REDIS_URL`) + `app/__init__.py` choose-bus.
  - D — SocketIO mirror subscriber + dedup LRU.

**Opus review gate:** ordering, replay-from-xid correctness, no message loss across reconnect,
delivery semantics (at-least-once + idempotent consumers = effectively-once for consensus).

## Stage 2 — Actor Isolation  [single-track for shared-state migration; parallel for container templates + manifests + tests]

**Files:** new `scripts/actor_runner.py` (per-actor entrypoint), `app/services/container_manager.py`
(extend for any actor kind), `app/models/agv_state.py` + `grid_state.py` (Redis-backed),
`docker-compose.yml`, tests.

Design fork to resolve in sub-spec:
- **Shared-state model.** Phase 3 put AGV motion in-backend with direct `agv_state`/`grid_state`
  access. When an AGV runs in its own container that breaks. Two options:
  - **(R)** Move shared state to Redis: `agv:<id>` hash for AGV record; lane/station locks via
    Redis SET NX + Lua. Cleanest, matches "actor isolation"; locks survive a coordinator-less
    architecture. **Proposed.**
  - **(H)** Actor calls backend HTTP for state writes + locks. Smaller jump, but the backend
    becomes a hidden coordinator for shared mutations — drift toward the anti-pattern.

Per-actor container = small process running `scripts/actor_runner.py --actor-id <id>` which
loads its `CapabilityProfile` from a config endpoint (or `pipeline.yaml` mount) and runs the
event loop against the Redis bus. Label-owned by `container_manager.reconcile_actors` (already
scale-symmetric for arms; extended to handle AGVs).

Parallel within stage:
- A (single track): shared-state migration to Redis (locks + state) — this is consistency
  surface; one focused implementation + tests.
- B ‖ C ‖ D (concurrent after A's contracts land):
  - B — `actor_runner.py` + container build.
  - C — `container_manager` AGV extension + compose update.
  - D — integration tests (actor in container talks to Redis bus to backend).

**Opus review gate:** authority-boundary — actors must own decisions; the backend must hold
no orchestration role. Distributed-lock correctness (no double-grant, fair release).

## Stage 3 — Concurrent Negotiations  [single track — consensus invariants]

**Files:** `app/agents/actor_agent.py`, `app/routes/agv_routes.py` (`_signals`), tests.

The H9 gotcha. Concrete refactor — encapsulated `TaskParticipant` (state isolation, not
orchestration):
- New `TaskParticipant` object owns: `task`, `round`, `evaluated_round`, `my_slots`,
  `executed_legs`, `reopened_rounds`, `consensus_announced`, `failure_announced`,
  `replay_cursor` (xid checkpoint — Stage 5), `halt_flag`. Methods: `on_announce`,
  `place_bids`, `evaluate`, `check_consensus`, `check_failure`, `maybe_reopen`,
  `execution_valid`. Reads only from `self.bus.history()` — leaderless rule unchanged.
- `ActorAgent`: keeps lifecycle + bus + dispatcher. New `self._tasks: dict[task_id,
  TaskParticipant]`. Event loop routes each message to `self._tasks[msg.task_id]`.
  `_check_deadlines` iterates participants. No more bare `self._round` reads anywhere.
- `_signals`: rekey from `{agv_id: ...}` to `{(task_id, agv_id): ...}`; promoted handshake
  functions take `task_id`.
- Concurrency cap (Phase 4): N concurrent tasks per actor (config). Bidding eligibility may
  consider whether the actor already holds a slot in another task (`capacity`).

Justification (per the Phase 4 fork): Phase 5 will need replay / recovery / reconvergence /
partial-execution / halted-legs / reconnect-adoption / checkpoint-replay — all task-scoped
state machines. A flat dict makes those leak across tasks; an encapsulated participant
contains them. Same leaderless invariant, smaller audit surface.

Parallelization: NONE. Consensus surface.

**Opus review gate:** signal scoping, reopen collision absence, no state leakage across tasks,
deterministic winner selection unchanged.

## Stage 4 — Persistent Runtime State  [single track for log semantics; parallel for tooling]

**Files:** `app/agents/negotiation.py` (new MessageTypes), `actor_agent.py` (emit transitions),
new persistence helpers, tests.

Design:
- The Redis Streams XADD log from Stage 1 IS the persistent event history for negotiation
  protocol (rounds, bids, accepts, withdraws, faults, consensus, failures). Free.
- ADD missing protocol-level events as new `MessageType`s: `LEG_STARTED`, `LEG_COMPLETED`,
  `LEG_HALTED` — emitted by `_check_consensus` / `_execute_leg`. Now execution transitions
  are part of the persistent log without a parallel store.
- Persisted `_executed_legs` per (actor_id, task_id) keyed in Redis Hash for genuine
  cross-restart de-duplication.

Parallel within stage:
- A (single track): MessageType additions + emit sites + replay-correctness invariants.
- B ‖ C (concurrent): persistence helpers, audit/debug tooling, replay-from-log unit tests.

**Opus review gate:** replay determinism (replaying the log from xid 0 produces the same state).

## Stage 5 — Distributed Recovery  [single track — recovery semantics]

**Files:** `actor_agent.py` (reconnect/replay path), `agent_registry.py` (per-process startup
becomes per-container; central registry stops owning lifecycle), heartbeat protocol, tests.

Design:
- Actor crash → container restarts → process boots → loads last `checkpoint_xid` from Redis →
  XREADGROUP resumes from PEL (auto-redelivers anything un-ACKed before crash) → replays
  history → re-derives task state → resumes.
- Dead-agent detection: periodic `AVAILABILITY_UPDATE` (already in MessageType enum, unused
  today) every 5 s. Stale > N×interval ⇒ peers observe absence; if the dead agent held a
  slot, fault/withdraw is naturally inferred (its locks expire via Redis EXPIRE TTL).
- Stale execution: the existing `_execution_valid` barrier already covers this; in distributed
  setting the barrier re-reads bus.history() from Redis, so a superseded round detected post-
  reconnect halts mid-leg before the next chunk.
- Network partition: best-effort with at-least-once + idempotency. Hard split-brain prevention
  via single Redis cluster (Phase 4 assumes one Redis; multi-region is Phase 5).

Parallelization: NONE for the protocol; cleanup/wiring can be Haiku.

**Opus review gate:** crash recovery, reconnect replay correctness, stale-execution halt under
network jitter, no orphaned locks.

## Stage 6 — Replay & Observability  [PARALLEL — many independent tools]

**Files:** new `app/routes/replay_routes.py`, frontend new `RuntimeReplay` panel + timeline
viz + fault history + actor inspector, docs.

Design:
- Backend `GET /bus/replay?from=<xid>&to=<xid>&task_id=<id>` returns XRANGE slice.
- Frontend Replay panel: scrub a timeline, render events in order, show derived round/consensus
  state at each XID. Reuses the existing typed `bus_message` rendering — replay = re-feed
  historical messages into the same renderer.
- Timeline viz: per (task_id, round) lane; bids/accepts/withdraws/faults annotated.
- Actor inspector: list actors with last `AVAILABILITY_UPDATE` ts, last consumed xid, current
  state.

Parallel within stage (4 concurrent agents):
- A — replay endpoint + XID slicing.
- B — frontend Replay panel + timeline.
- C — frontend Fault history + actor inspector views.
- D — observability docs + screenshots.

**Opus review gate:** no orchestration authority leaking into "observation" UI. Replay shows
history, never decides.

## Stage 7 — Distributed Integration Testing  [single track — correctness; parallel for individual scenarios]

**Files:** new `tests/distributed/` dir, CI compose for Redis, toxiproxy or netem for chaos.

Scenarios (each = one focused test):
- Actor crash mid-consensus → reconnect → reconverge with correct holder.
- Duplicate Redis delivery → consumer dedup → consensus stable.
- Reconnect after backend restart → catch-up replay from last xid.
- Stale message replay (XADD a historical message id post-reconnect) → ignored by monotonic-round guard.
- Concurrent negotiations → no signal corruption, no reopen collision.
- Distributed timeout drift (skewed clocks via container time offset) → consensus_deadline_ts
  honoured by each actor independently.
- Split-brain prevention (Redis isolated from one actor) → that actor halts safely (barrier),
  no rogue execution.

Parallel within stage: 7 scenario implementations concurrent; the test harness (Redis
container, toxiproxy fixtures) is single-track shared infra.

**Opus review gate:** every scenario asserts the invariant explicitly; no false greens.

---

# Decisions needing your sign-off (per stage)

1. **Stage 1** — stream layout: one global `interact:negotiation` vs per-task streams.
   **Proposed: one global.** Per-task can be added in Phase 5 if scale demands.
2. **Stage 1** — consumer-group model: one group per actor.
   **Proposed: yes** (preserves `subscribe_all` semantics, gives at-least-once + PEL recovery).
3. **Stage 2** — shared-state model: Redis-backed locks/state (R) vs HTTP-mediated (H).
   **Proposed: R** — Redis-backed. H reintroduces backend as a hidden coordinator for state
   writes; R is the architecturally honest fit for actor isolation.
4. **Stage 3** — ActorAgent refactor scope: **lightweight `TaskParticipant` object** (approved
   refinement). Encapsulate per-task state inside an isolated object — round, accepts,
   execution state, deadlines, reopen state, replay cursor, halt flags, checkpoints. NOT a
   separate orchestration layer; still actor-owned, still leaderless. Justification: Phase 5's
   replay / recovery / reconvergence / partial-execution / halted-legs / reconnect-adoption /
   checkpoint-replay are all task-scoped state machines. A flat `_tasks: dict` mutation jungle
   would explode under that surface; an encapsulated participant object keeps the leaderless
   invariant and makes per-task rehydration tractable.
5. **Stage 4** — execution-transition log: new MessageTypes on the existing bus vs separate
   stream. **Proposed: new MessageTypes** (`LEG_STARTED`/`LEG_COMPLETED`/`LEG_HALTED`) —
   single source of truth, replay covers execution automatically.
6. **Stage 5** — dead-agent detection: heartbeat (`AVAILABILITY_UPDATE`) vs Redis XAUTOCLAIM
   only. **Proposed: heartbeat + lock TTLs.** XAUTOCLAIM alone leaves held-state ambiguous.
7. **Stage 7** — chaos tooling: toxiproxy (HTTP proxy with fault injection) vs raw kill-9 +
   docker network disconnect scripts. **Proposed: toxiproxy** for network faults + raw
   kill-9 for process crashes.

# Constraints (from Phase 4 spec)

- No hidden coordinators, no centralized replay authority, no faked distributed behaviour, no
  hidden orchestration shortcuts. Protect decentralization aggressively.
- Bus transports shared reality only; it does not decide consensus.
- Recovery is protocol-driven, not coordinator-driven.
- Event log is shared reality, not orchestration authority.

# Deferred to Phase 5 (must NOT implement)

adaptive AI bidding, semantic negotiation, reinforcement learning, autonomous manipulation,
emergent optimization, computer vision, multi-region Redis.

# Parallelization summary

True multi-agent parallel execution within each stage where the spec marks it safe; bounded
by the stages' mandatory sequence (1→7) and the no-parallel surfaces (consensus, recovery,
consistency, replay guarantees).

| Stage | Single-track surface | Parallelizable |
|---|---|---|
| 1 | adapter + replay semantics (A) | manifest, env wiring, mirror (B/C/D) |
| 2 | shared-state migration (A) | container template, reconcile ext, tests (B/C/D) |
| 3 | every consensus refactor | none |
| 4 | log semantics + replay determinism | tooling, audit, persistence helpers |
| 5 | recovery protocol | cleanup, wiring |
| 6 | none | replay endpoint, timeline, fault view, docs (4 concurrent) |
| 7 | shared chaos harness | 7 scenario implementations concurrent |

# Risk register (for review gates)

- **Race on consensus across processes** — Stage 1 must prove ordered delivery per stream and
  that `bus.history()` snapshots are linearizable enough for `surviving_bids`/`accepted_actors`
  to compute identically across nodes.
- **Lock TTL vs slow leg** — Redis lock with too-short TTL kills a legitimate slow leg; too long
  leaves orphans. Tune; the existing `LaneLock` `cancel_check` pattern carries over.
- **Heartbeat false-positive deaths** — under GC pauses or network jitter, a live actor is
  presumed dead. Use a generous interval (5 s) and N=3 strikes.
- **Replay non-determinism** — execution transitions must be a function of history only;
  cosmetic emits (SocketIO mirror) must not influence consensus.
