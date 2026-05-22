# INTER.act — Phase 1 Implementation Audit (Post-Cutover)

**Audit date:** 2026-05-23
**Branch:** `dev-sneh`
**Auditor note:** This document is the post-Phase-3 counterpart to
`ARCHITECTURE_REALITY_AUDIT.md` (pre-cutover, 2026-05-22). Each section
explicitly states which pre-cutover contradictions were resolved and which
were intentionally deferred.

---

## 1. Executive Verdict

INTER.act now runs a **decentralized negotiation runtime**. The architectural
cutover is real, not cosmetic:

- `CentralBrain`, `plan_executor`, the old emit-only `message_bus`, the
  centralized `/plan` entry point, and the AGV container services (`agv-1/2/3`)
  were deleted. No surrogate coordinator replaced them.
- Every actor (arm and AGV) is an autonomous backend thread that subscribes to
  a typed `NegotiationBus`, evaluates the public bid set with the same
  deterministic rule, and independently arrives at the same winner without any
  component making a "decision" for them.
- Actor execution (arm angle sweeps, AGV cell-by-cell movement) runs in-backend
  on each agent's thread. The execution barrier re-checks validity before every
  motion chunk; a superseding round halts it.
- The frontend `BusPane` now renders real typed `bus_message` SocketIO traffic
  — `task_announce`, `bid`, `accept`, `consensus_reached`, `task_failed`, etc.
  — not regex-relabeled logs.

**What changed from the pre-cutover audit's critical findings:**

| Pre-cutover finding | Status after Phase 3 |
|---|---|
| C1 — CentralBrain is a single LLM call assigning all actors | Resolved — `central_brain.py` deleted; no actor is assigned by any component |
| C2 — No capability broadcast, no bid, no P2P | Resolved for Phase 1 scope: capability broadcast + bid/accept/withdraw/consensus implemented; P2P is deferred to Phase 2 |
| C3 — `agv_fault` never published; `reassign()` dead code | Resolved — the entire centralized re-planning path deleted; decentralized reopen-on-fault implemented |
| H2 — AGVs reintroduced as compose services | Resolved — `agv-1/2/3` services removed; agents run in-backend |
| M1 — Broken AI-layer startup wiring | Resolved — `app/__init__.py` now calls `agent_registry.start_agents()` cleanly |
| M2 — BusPane is regex over logs | Resolved — `classifyBus` deleted; BusPane renders real typed events |

---

## 2. What Centralized Authority Was Removed

### Deleted modules

| Module | Centralized authority it embodied |
|---|---|
| `app/agents/central_brain.py` | "Master Planner AI" — a single `qwq:32b` call that assigned every package to an AGV, fixed execution order, defined routes, and assigned arm roles. The textbook central orchestrator. |
| `app/services/plan_executor.py` | The dispatch arm of `CentralBrain` — sorted assignments by `sequence` and `POST`ed each to an AGV container's `/assign`. Distributed a centrally-made plan. |
| `app/agents/message_bus.py` (old) | Emit-only telemetry fan-out masquerading as a negotiation bus. No actor could subscribe; it carried no bids, no capability, no negotiation messages. |
| `scripts/agv_brain.py` | AGV tactical brain — a centralized 12-step hardcoded sequence. The LLM gate was advisory-only (any timeout → proceed); the route was deterministic geometry. |
| `scripts/agv_server.py` | AGV container server — the Flask app that received centralized `/assign` commands and ran the hardcoded steps. Each AGV was an execution worker for central orders. |
| `app/routes/dispatch_routes.py` | Centralized arm animation driver — `_auto_arm_signals` swept hardcoded angle lists independent of any negotiation; "works even without real arm containers." |

### Deleted routes

| Route | Authority it embodied |
|---|---|
| `POST /plan` | The central planning entry point: interpret → `CentralBrain.make_plan` → `plan_executor.dispatch_plan`. Replaced by `POST /negotiate`. |
| `GET /coord/snapshot` | Central shared-state poll. AGVs read this instead of messaging peers. |
| All of `dispatch_routes` blueprint | Centralized arm animation and demo dispatch. |

### Deleted tests

`tests/test_central_brain.py`, `tests/test_agv_brain.py` — unit tests for the
now-deleted centralized components.

### Deleted compose services

`agv-1`, `agv-2`, `agv-3` removed from `docker-compose.yml`. AGV execution is
now in-backend threads; no AGV container lifecycle.

---

## 3. How Decentralized Negotiation Works Now

### Entry point

`POST /negotiate` (`app/routes/negotiate_routes.py:62`) accepts a plain-English
prompt, calls `interpreter.interpret()` to produce a `TaskSpec`, resolves source
and destination station zones from `pipeline_config.get_stations()`, calls
`derive_slots()` to build the three structural slots (`pickup`/`transport`/`place`),
assembles a `TASK_ANNOUNCE` `Message`, and publishes it to `get_bus()`. The
route returns **202 immediately**. It assigns nothing, sequences nothing, and
has no knowledge of which actors exist.

### Capability broadcast and bidding

Every agent's `_on_task_announce` (`actor_agent.py:199`) fires on the announce.
Each calls `_broadcast_capability()` (publishes `CAPABILITY_BROADCAST` with its
profile) then `_place_bids()` (`actor_agent.py:229`): iterates the announce's
slot list, calls `_bid_score(slot)`, and publishes a `BID` message for each
slot it can fill. `ArmAgent._bid_score` returns `0.0` when the slot's zone is
in `profile.reachable_zones`, `None` otherwise (`arm_agent.py:28`).
`AGVAgent._bid_score` returns the Manhattan distance from the AGV's current
cell to the pickup station dock (`agv_agent.py:33`). No actor is told to bid;
no component decides eligibility.

### Leaderless evaluation and the `public_winner` rule

At `bid_deadline_ts`, each agent's `_evaluate()` (`actor_agent.py:281`) fires.
It calls `surviving_bids(history, task_id, round, slot_id)` for each slot —
a pure function over the public bus history that keeps only the latest
`bid_version` per sender and drops any sender that has since withdrawn
(`negotiation.py:120`). It then calls `public_winner(bids, constraint)`
(`negotiation.py:85`):

- If the constraint is `"shortest time"` or `"shortest distance"`: lowest score
  wins; ties broken by lowest `sender` id (deterministic).
- Otherwise: lowest `sender` id among valid bidders (deterministic).

Every agent calls this function with the same `surviving_bids` set and the same
constraint. Every agent computes the identical winner. No component decides; the
rule does. The winner publishes `ACCEPT`; losers publish `WITHDRAW`. This is the
leaderless-consensus mechanism.

### Consensus observation

`_check_consensus()` (`actor_agent.py:331`) is called in the event loop after
evaluation. It reads `accepted_actors(history, task_id, round)` — the map of
`slot_id → accepting actor id` derived from bus history by scanning `ACCEPT` and
`WITHDRAW` messages in publish order (`negotiation.py:153`). It calls
`slots_complete(required, accepts)` (`negotiation.py:173`): returns `True` only
when `required_slot_ids == set(accepts.keys())` and all values are non-empty.

When complete, each agent that observes it publishes `CONSENSUS_REACHED` exactly
once (guarded by `_consensus_announced`). This is an **idempotent observational
marker**, not an authority message — see §4.

### Fault-driven reopen

When a holding agent faults or withdraws after the bid deadline, any non-faulted
agent may call `_maybe_reopen()` (`actor_agent.py:461`). It checks that a
previously-accepted slot has since lost its holder. If so, it publishes a new
`TASK_ANNOUNCE` at `round + 1`, with `announce_ts` set to `trigger.ts` — the
trigger message's timestamp. Every agent that reopens uses the same `trigger.ts`,
so concurrent reopens from different agents are **byte-identical** (idempotent)
and the bus deduplicates them. The faulted agent re-enters `FAULTED` state and
does not rejoin (`actor_agent.py:201`).

### Deadline failure via `TASK_FAILED`

`_check_failure()` (`actor_agent.py:375`) fires when `consensus_deadline_ts` has
passed, no consensus was observed, no reopen for `round + 1` is already pending,
and at least one required slot has zero surviving bids. Each qualifying agent
publishes `TASK_FAILED` once (guarded by `_failure_announced`). The reason is
`"no viable bids"` (a slot is unfillable) or `"consensus deadline expired"`. This
is `MessageType.TASK_FAILED` (`negotiation.py:31`) — the 12th message type, a
pure observational marker parallel to `CONSENSUS_REACHED`.

---

## 4. How Consensus Emerges

The Phase 1 audit's key complaint was: *"no negotiation of any kind exists —
`CentralBrain` assigns."* The mechanism now is genuinely different:

1. **No component assigns.** `public_winner` (`negotiation.py:85`) is a pure
   function. It does not run in any single "decider" — it runs on every agent's
   thread independently, with the same input, producing the same output.

2. **Each agent self-selects.** A winning agent publishes `ACCEPT` not because
   it was told to, but because its own evaluation of the public rule determined
   it won. A losing agent publishes `WITHDRAW` by the same logic.

3. **`CONSENSUS_REACHED` is observational.** It is published by each agent that
   independently notices `slots_complete()` is true. It does not instruct
   execution to begin. The receiving agents do not act on `CONSENSUS_REACHED` —
   they act on `slots_complete()` being true in their own `_check_consensus()`
   call. The message is a bus-visible marker for the frontend and for tests; it
   carries no authority.

4. **`TASK_FAILED` is the same pattern.** Each agent that independently observes
   the failure condition publishes it once. No agent acting on a `TASK_FAILED`
   message is mandated to do anything — the frontend observes it.

5. **Deterministic ties are structural.** The tiebreak (`min(sender)`) is a
   property of the rule, not a coordinator's choice. Two agents computing the
   same `surviving_bids` will always compute the same winner.

---

## 5. Execution Model

### In-backend agents

All agents are daemon threads started by `agent_registry.start_agents()`
(`app/__init__.py:77`) at Flask startup, registered for clean shutdown via
`atexit.register(stop_agents)`. There are no AGV containers; the agent threads
are the runtime.

### AGVAgent execution

`AGVAgent._execute_leg("transport")` (`agv_agent.py:61`) implements the 12-step
transport sequence directly against `agv_state` and `grid_state`. Route geometry
is a ported `_straight_path()` rectilinear helper (`agv_agent.py:339`). The
`MOVE_STEP_SLEEP = 0.5 s` cadence keeps the frontend easing from stalling.

The load/unload handshake uses promoted public functions from `agv_routes.py`:
`signal_load(agv_id, object_id)`, `poll_load(agv_id)`, `clear_load(agv_id)`,
and the unload trio (`signal_unload`, `poll_unload`, `clear_unload`). These
reuse the existing `_signals` dict and `_signals_lock` — no parallel registry.
The HTTP routes call the same functions.

### ArmAgent execution

`ArmAgent._execute_leg()` (`arm_agent.py:38`) reads the winning AGV id from
`accepted_actors(history, task_id, round)["transport"]` on the live bus. For
`pickup`: `sweep_arm(arm_id, PICK)` → `sweep_arm(arm_id, REST)` →
`signal_load(agv, object_id)`. For `place`: `signal_unload(agv)` → wait for AGV
to clear → `sweep_arm(arm_id, PLACE)` → `sweep_arm(arm_id, REST)`.

`sweep_arm()` lives in `app/agents/actuation.py`. It calls `arm_state.set_angle`
and emits `arm_angle_update` per step, with an optional `halt_check` callable for
barrier integration.

### Emergent sequencing — no central sequencer

The transport sequence emerges from the handshake:

- AGV step 3 (`poll_load`) blocks until the pickup arm's `signal_load` fires.
- AGV step 9 (`poll_unload`) blocks until the place arm's `signal_unload` fires.

No component orders these steps. The coordination is a consequence of the
lock/signal structure, not of any scheduler.

### Execution barrier (`_execution_valid`)

Before each major step and between every motion chunk (cell hop or angle step),
`_execution_valid(slot_id)` (`actor_agent.py:406`) is called. It re-derives from
a fresh `bus.history()`:

1. Consensus still complete (`slots_complete(required, accepts)`).
2. This agent still holds `slot_id` (`accepts[slot_id] == self.actor_id`).
3. No higher-round `TASK_ANNOUNCE` exists for this `task_id`.
4. No `_fault_requested` flag.

On `False`: the agent halts immediately, releases held `grid_state` locks (lane
and station), and logs a halt line. The leg returns `False`; it is not recorded
in `_executed_legs`; if the same agent re-wins the slot on a reopened round it
will re-execute.

---

## 6. What Remains Centralized — Intentionally

### The Interpreter LLM

`app/agents/interpreter.py` makes one `qwq:32b` call to convert a plain-English
prompt into a `TaskSpec`. This is a **single centralized model call**. It is
acceptable because:

- It interprets *human intent* — it maps natural language to a structured task
  description. That is an input-contract concern, not an orchestration concern.
- It does NOT assign actors, define routes, fix execution order, or specify which
  AGV carries which package. `TaskSpec` carries `intent`, `package_count`,
  optional `constraints` — not assignments.
- The pre-cutover audit's complaint about `CentralBrain` was that it assigned
  actors via an LLM call. The Interpreter does not.

### `task_spec.py` and `plan_validator.py`

These are validation firewalls — single sources of truth on schema. They do not
authority over execution; they reject malformed input. This centralization is
defensive, not coordinative.

### The negotiation bus as an in-process singleton

`get_bus()` (`bus.py:89`) returns a process-wide `NegotiationBus` singleton.
This is a **Phase 1 architectural limit**: the bus is an in-process Python object
with a threading lock. In a true distributed system each agent would connect to a
shared message broker. That is explicitly deferred to Phase 2.

The singleton is not a coordinator — it has no decision logic, no subscribers
list that it controls, no authority. It records history, delivers messages to
subscribed callbacks, and mirrors to SocketIO. The authority is in the agents'
evaluation of the history, not in the bus.

### One task at a time

The `_task_id` / `_round` per-agent state tracks one active negotiation.
Concurrent negotiations are Phase 2 scope. This is an explicit Phase 1 limit,
not a hidden centralization.

---

## 7. What Is Still Transitional — Honest Assessment

### `_signals` registry is keyed by `agv_id`

`agv_routes._signals` is a `dict[str, dict]` keyed by AGV id. In Phase 1 (one
task at a time) this is correct. Concurrent negotiations would need per-task
keying to avoid signal collisions. Not a current bug; a deferred constraint.

### Halted-then-reopened legs re-execute on the same agent

Stage 3 Bug 2 fix: when an agent re-wins the same slot on a reopened round, it
WILL re-execute the physical leg. This is intentional and correct for Phase 1.
Cross-agent recovery (a different agent taking over a partially-completed leg)
is Phase 2.

### AGV execution is in-backend, not "container per actor"

`scripts/agv_server.py` was deleted. The `AGVAgent` thread runs inside the
backend process. This is pragmatic — it eliminates the container-per-AGV
complexity and the centralized-dispatch coupling — but it is not the "container
per actor with full failure isolation" model that the unified actor-runtime
architecture describes for arms. Converging AGV execution to the label-owned
container model is Phase 2.

### Frontend mock-mode panels

Some frontend panels still have mock-mode fallback data. The mock driver
correctly mutates mock runtime; live mode shows real data. Empty panels in mock
mode are a UX gap, not an architectural contradiction.

### Warehouse grid still comes from `pipeline.yaml`

The warehouse topology (grid dimensions, station positions, lane cells, AGV bay
positions) is still configured in the `warehouse:` block of `pipeline.yaml`. It
is not yet author-able on the canvas. This is a frontend authoring gap inherited
from before Phase 3.

### 5 pre-existing test failures

`tests/test_arm_execution.py` (×1) and `tests/test_warehouse_config.py` (×4)
fail. These pre-existed Phase 3 and are unrelated to the negotiation runtime.
Phase 3 left them untouched. Current suite: **248 passed, 5 failed**.

---

## 8. Evidence Index

| Claim | File:line |
|---|---|
| `TASK_FAILED` — 12th observational message type | `app/agents/negotiation.py:31` |
| `public_winner` — deterministic leaderless rule | `app/agents/negotiation.py:85` |
| `surviving_bids` — pure function over bus history | `app/agents/negotiation.py:120` |
| `accepted_actors` — slot→actor map from history | `app/agents/negotiation.py:153` |
| `slots_complete` — validation only, never picks actor | `app/agents/negotiation.py:173` |
| `derive_slots` — structural slot decomposition | `app/agents/negotiation.py:185` |
| `BID_WINDOW_S = 1.5`, `CONSENSUS_WINDOW_S = 1.5` | `app/agents/negotiation.py:79` |
| Agent event loop, `_next_wakeup`, inbox queue | `app/agents/actor_agent.py:137` |
| `_on_task_announce` — round adoption, FAULTED guard | `app/agents/actor_agent.py:199` |
| `_broadcast_capability` + `_place_bids` | `app/agents/actor_agent.py:223`, `:229` |
| `_evaluate` — leaderless accept/withdraw | `app/agents/actor_agent.py:281` |
| `_check_consensus` — observational, not authoritative | `app/agents/actor_agent.py:331` |
| `_check_failure` — `TASK_FAILED` observational marker | `app/agents/actor_agent.py:375` |
| `_execution_valid` — barrier: 4-condition re-check | `app/agents/actor_agent.py:406` |
| `_maybe_reopen` — byte-identical announce_ts from trigger | `app/agents/actor_agent.py:461` |
| `ArmAgent._bid_score` — zone-based eligibility | `app/agents/arm_agent.py:28` |
| `ArmAgent._execute_leg` — pickup/place with barrier | `app/agents/arm_agent.py:38` |
| `AGVAgent._bid_score` — Manhattan distance | `app/agents/agv_agent.py:33` |
| `AGVAgent._execute_leg` — 12-step with barrier checks | `app/agents/agv_agent.py:61` |
| `_straight_path` ported from `agv_brain` | `app/agents/agv_agent.py:339` |
| `sweep_arm` — angle sweep with halt_check | `app/agents/actuation.py:38` |
| `PICK=180`, `PLACE=0`, `REST=90` | `app/agents/actuation.py:23` |
| `NegotiationBus` — record + mirror + dispatch | `app/agents/bus.py:21` |
| `get_bus()` — in-process singleton | `app/agents/bus.py:89` |
| `start_agents()` — build ArmAgent/AGVAgent per profile | `app/agents/agent_registry.py:24` |
| `stop_agents()` joins threads outside registry lock | `app/agents/agent_registry.py:45` |
| `POST /negotiate` — entry point, 202 immediate | `app/routes/negotiate_routes.py:62` |
| Route assigns nothing, sequences nothing | `app/routes/negotiate_routes.py:1` (module docstring) |
| `signal_load`, `poll_load`, `clear_load` — promoted public | `app/routes/agv_routes.py` |
| `start_agents()` called at startup + atexit | `app/__init__.py:77` |
| Test suite result | 248 passed, 5 pre-existing failures |

---

*End of Phase 1 Implementation Audit.*
