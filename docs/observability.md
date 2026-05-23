# Observability — Read-Only Bus & Actor Inspector

## 1. Overview

INTER.act's observability layer makes the decentralized negotiation runtime transparent
without orchestrating it. The frontend is an observer; replay never mutates state, the
inspector never commands actors.

Three layers:
- **Backend replay endpoints** (`/bus/replay`, `/bus/actors`) — filtered pure reads over
  `bus.history()`.
- **Frontend Replay panel + Timeline** — scrub through the log, plot events on per-task
  lanes (`frontend/src/chrome/Replay/`).
- **Frontend Fault history + Actor inspector** — grouped fault rendering + per-actor
  state snapshot with presumed-dead flag (`frontend/src/chrome/Observability/`).

Pure reads. No XADD, no XACK, no state mutation, no decision authority from this layer.
Decisions are leaderless and live in actor code; observability views their outcome.

See `CLAUDE.md` (`Interpreter parses intent. Actors own decisions. Bus owns shared
reality. Consensus emerges.`) and `PHASE_1_IMPLEMENTATION_AUDIT.md` (the leaderless
invariants) for the underlying protocol guarantees.

---

## 2. MessageType reference (15 types)

All 15 types live on the same `NegotiationBus` and the same Redis Stream
`interact:negotiation`. None are decision-bearing across actors — every actor reads its
own `bus.history()` and derives the same answer via `public_winner` /
`accepted_actors` / `slots_complete`.

| Type | Class | Sender | Semantics |
|---|---|---|---|
| `task_announce` | structural | interpreter or reopening agent | Task introduced; payload carries slots + deadlines. `negotiation_round > 0` is a reopen (`app/agents/actor_agent.py` `_maybe_reopen` / `task_participant.py`). Reopens are byte-identical idempotent (announce_ts = trigger ts). |
| `capability_broadcast` | structural | agent | "I can do X" for this task. Sent on `task_announce` receipt. |
| `availability_update` | observational | agent | Heartbeat. Every 5 s. Payload `{ts, state}`. Used only for observation; never consulted for consensus. |
| `bid` | structural | agent | "I bid score Y for slot X." Posted within `BID_WINDOW_S` of announce. |
| `counter_offer` | reserved | agent | Defined; unused in Phase 1/4. Reserved for semantic counter-offers (Phase 5+). |
| `accept` | structural | agent | "I hold slot X." Posted by every agent that locally computes itself as winner via `public_winner`. |
| `reject` | structural | agent | Defined; rarely emitted in current code path — withdraw is the active loser signal. |
| `busy` | structural | agent | "I cannot bid (already executing)." Optional participation gate (Phase 4 cap). |
| `fault` | observational | agent | "I halted execution (barrier failure, external fault)." Faulted agent stays out of further rounds in Phase 4. |
| `withdraw` | structural | agent | "I release my held slot." Triggers `_maybe_reopen` checks on other agents (`app/agents/task_participant.py`). |
| `consensus_reached` | observational | agent | "I locally observe all slots filled." Every agent publishes independently; idempotent per participant (`consensus_announced` flag). Not an authority claim. |
| `task_failed` | observational | agent | "consensus_deadline_ts expired with consensus incomplete." Every agent publishes independently; idempotent per participant (`failure_announced`). Not an authority claim. |
| `leg_started` | observational | agent | "I am about to call `_execute_leg(slot_id)`." Phase 4 Stage 4. |
| `leg_completed` | observational | agent | "`_execute_leg(slot_id)` returned True." Used for cross-restart dedup via `_rehydrate_executed_legs`. |
| `leg_halted` | observational | agent | "`_execute_leg(slot_id)` returned False (barrier failure or fault)." |

**Observational vs structural distinction.** Structural messages drive agent decisions
(every actor evaluates the same bid set and publishes accept/withdraw deterministically).
Observational messages witness state — they are bus-visible audit trails and frontend
fodder; agents do not change consensus based on another agent's observational message.

---

## 3. Replay endpoint contract

`GET /bus/replay?from=<i>&to=<i>&task_id=<id>&type=<t>&limit=<n>`

All query parameters optional. Pure read.

| Param | Type | Default | Behaviour |
|---|---|---|---|
| `from` | int (index) | `0` | Start at this 0-based index into `bus.history()`. |
| `to` | int (index) | last | Inclusive upper index. |
| `task_id` | string | none | Exact match filter. |
| `type` | string | none | Exact `MessageType` value filter (e.g. `bid`, `consensus_reached`). |
| `limit` | int | 1000 (max 10000) | Caps the returned payload. |

Response:
```json
{
  "messages": [
    {"type": "bid", "task_id": "...", "negotiation_round": 0, "sender": "agv-1",
     "slot_id": "transport", "bid_version": 0, "ts": 1234567890.123, "payload": {...}},
    ...
  ],
  "count": 42,
  "bus": "inprocess" | "redis"
}
```

XID slicing for the Redis bus is best-effort via index; full XID range slicing is a
future enhancement (`app/routes/replay_routes.py`).

---

## 4. Actor inspector endpoint

`GET /bus/actors`

Reports the heartbeat-derived view of every actor that has ever published.

Response:
```json
{
  "actors": [
    {"actor_id": "arm-1", "last_seen": 1234567890.5, "state": "idle",
     "presumed_dead": false},
    {"actor_id": "agv-2", "last_seen": 1234567866.1, "state": "executing",
     "presumed_dead": true}
  ],
  "threshold_s": 15.0
}
```

| Field | Derivation |
|---|---|
| `last_seen` | `app/agents/heartbeat.py:last_seen(history, actor_id)` — latest `availability_update.payload.ts` from this sender. |
| `state` | `payload.state` from the latest `availability_update` from this sender. Null if no heartbeat seen. |
| `presumed_dead` | `app/agents/heartbeat.py:is_presumed_dead(history, actor_id, 15.0)` — true when `now - last_seen > 15.0`. |
| `threshold_s` | Constant 15.0 (= 3 × `HEARTBEAT_INTERVAL_S` 5.0). |

Only senders starting with `arm-` or `agv-` are returned — `interpreter` and other
non-actor senders are excluded.

`presumed_dead = true` is REPORTED, never acted on by the protocol. Lock recovery
(orphans expire by Redis TTL — `app/models/redis_lock.py`) and consensus recovery
(fault → reopen via bus messages) flow independently of heartbeat observation.

---

## 5. Timeline interpretation

`frontend/src/chrome/Replay/Timeline.tsx` lays out history per `(task_id, round)` lane.

- Each lane = one round of one task. A new lane appears for each `task_announce` whose
  `negotiation_round > 0` (a reopen).
- Events plotted along time axis by `ts`. Color/tone matches the BusPane chip mapping
  (`frontend/src/chrome/LogsPanel/busMessageChip.ts`).
- `consensus_reached` markers and `task_failed`/`leg_halted` markers are visually
  distinct (vertical line / red marker) so that successful and failed rounds are
  immediately readable.
- Click an event → fires `onSelect(index)` so the Replay cursor jumps to that point.

A multi-lane task indicates reopens. Multiple `task_failed` markers across rounds of
the same task indicates a structurally unfillable task that exhausted retries.

---

## 6. Fault history interpretation

`frontend/src/chrome/Observability/FaultHistory.tsx` filters
`fault | withdraw | task_failed | leg_halted` from `/bus/replay` and groups by
`task_id`.

- Per task: collapsible card with count + most-recent fault type.
- Expand to show each message with its `payload.reason` if present.
- Empty state: "No faults observed".

Common patterns:
- One `withdraw` followed by a reopen `task_announce` (higher round) + new
  `consensus_reached` = normal fault-driven reconvergence.
- One `task_failed` without a subsequent reopen = exhausted retries (Phase 4 has one
  retry budget per fault; tunable).
- A burst of `leg_halted` from one actor = its barrier kept firing (most likely a
  higher round repeatedly arrived during execution — `_execution_valid` worked).

---

## 7. Heartbeat semantics

- `availability_update` published by each `ActorAgent` every 5 s
  (`HEARTBEAT_INTERVAL_S`, `app/agents/actor_agent.py`).
- Payload: `{"ts": time.time(), "state": <AgentState value>}`.
- Threshold for `presumed_dead`: 15 s (= 3 × interval).
- Wired purely for observation. The runtime never gates a decision on a heartbeat —
  consensus runs over `bus.history()` regardless; locks release by TTL regardless;
  reopens happen on observed faults regardless.

`ActorAgent` stops the heartbeat thread cleanly on `stop()`. Heartbeat failures
(`BusUnavailable`) are silently dropped — the next interval retries.

---

## 8. What this layer is NOT

- **Not an orchestrator.** The replay panel shows what happened; it does not decide
  anything. No "force reopen" button, no "kill actor", no "force fault", no
  "manually assign slot".
- **Not a consensus authority.** `consensus_reached` and `task_failed` displayed in the
  UI are records of what each agent independently observed. The frontend does not
  declare consensus or failure; it shows the agents' declarations.
- **Not a health controller.** `presumed_dead` is a reported observation; no UI action
  restarts an actor. Container restart is reconciler-driven
  (`app/services/container_manager.py` `reconcile_actors`); recovery semantics live in
  the bus protocol + Redis lock TTLs.
- **Not the source of truth.** `bus.history()` is the audit trail. The source of truth
  for actor behaviour is the actor code (`actor_agent.py`, `task_participant.py`,
  `arm_agent.py`, `agv_agent.py`). Observability is a lens, not a spec.

---

**End — `docs/observability.md`**
