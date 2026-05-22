# Phase 1 — Decentralized Negotiation Foundation (Design Spec)

**Date:** 2026-05-22
**Status:** Approved design — ready for implementation planning
**Predecessor:** `ARCHITECTURE_REALITY_AUDIT.md` (Review Phase 1)
**Source requirements:** `PHASE_1_PROMPT.md`, `goal.md`

---

## 1. Objective

Replace the centralized planner path with a real decentralized negotiation foundation.
At the end of this phase:

- No central component owns execution authority, task allocation, or consensus.
- Every actor participates in negotiation itself.
- The message bus is a real negotiation layer, not telemetry or relabeled logs.
- Allocation emerges from actor interaction, not from a planner assignment.

This phase is an **architectural correction**, not feature work, optimization, scaling,
or UI polish. The governing principle (from `PHASE_1_PROMPT.md`): *optimize for
architectural truth — a primitive real decentralized system beats an advanced fake one.*

### The conceptual shift

```
BEFORE:  Goal → CentralBrain (one LLM call assigns AGVs, order, routes) → workers execute
AFTER:   Goal → Interpreter (normalizes intent only) → task announced on a shared bus
                → actors broadcast capability, bid, and converge by leaderless evaluation
                → winning actors execute their own leg
```

The bus becomes the **shared environment** actors coordinate through — they communicate
through a shared reality, not through a controller. The protocol separates **shared
truth** (every actor sees the same bus) from **shared authority** (no actor, and no
central component, decides for the others).

---

## 2. Resolved design decisions

Five forks were resolved during brainstorming and are now fixed:

| # | Decision | Choice |
|---|---|---|
| 1 | Arm actors | Arms become **in-process actor agents** — real negotiation participants; physical motion stays animated |
| 2 | Consensus | **Leaderless evaluation** — every actor independently applies one public rule to one public bid set |
| 3 | Bus transport | **In-process agents + in-process bus**; AGV agent drives its AGV container over HTTP for physical motion |
| 4 | Bid logic | **Deterministic** — no LLM in the negotiation loop; the LLM stays only in the Interpreter |
| 5 | `CentralBrain` | **Deleted** — authority removed, not renamed |

---

## 3. Architecture

### 3.1 Component map

**NEW — `app/agents/`**

| File | Purpose | Interface | Depends on |
|---|---|---|---|
| `bus.py` | Typed pub/sub negotiation bus; in-process delivery + SocketIO mirror for the UI | `subscribe(type, cb)`, `publish(Message)` | `socketio` (mirror only) |
| `negotiation.py` | Message envelope dataclasses, the 11 message types, the **public consensus rule** (`constraint → rule`), and a pure **completeness check** | `Message`, `public_winner(slot, bids, constraint)`, `slots_complete(...)` | — |
| `actor_agent.py` | Base `ActorAgent` — capability profile, bus subscribe/publish, lifecycle state machine, bid/accept/withdraw logic, round bookkeeping | `ActorAgent(profile, bus)`, `.start()` | `bus`, `negotiation` |
| `arm_agent.py` | Arm specialization — bids for pickup/place role slots; drives arm animation on win | subclass of `ActorAgent` | `actor_agent`, arm animation fn |
| `agv_agent.py` | AGV specialization — transport bid = distance/speed; commands its AGV container on win | subclass of `ActorAgent` | `actor_agent`, `agv_state`, AGV container HTTP |
| `agent_registry.py` | Instantiates one agent per actor from `pipeline.yaml`, starts agent threads at app startup | `start_agents()` | `pipeline_config`, agents |

**KEEP (unchanged):** `interpreter.py`, `task_spec.py`, `prompt_builder.py` (interpreter
prompt only), `llm_service.py`, `agv_state.py`, `grid_state.py`, `agv_server.py` /
`agv_brain.py` (the AGV container = the execution body the AGV agent commands),
`container_manager.py`, `health_monitor.py`, the frontend store and canvas.

**DELETE:** `central_brain.py`, `plan_executor.py`, `plan_validator.py`,
`tests/test_central_brain.py`; the central-planning body of `ai_routes.py /plan`; the
central dispatch trigger in `dispatch_routes.py` (the arm *animation* function is reused
by arm agents — only the central trigger is removed); `classifyBus` in
`frontend/src/lib/contract.ts`; the mock-bus fiction in `frontend/src/runtime/mockData.ts`.

**REWRITE:** `message_bus.py` → `bus.py` typed negotiation bus. This also fixes the
`SocketIOBus(socketio)` constructor `TypeError` at `app/__init__.py:75` (audit M1).

**REWORK:** `ai_routes.py` → `/negotiate` endpoint; `app/__init__.py` startup →
instantiate `agent_registry` instead of `CentralBrain`; `frontend/.../BusPane.tsx` →
render real typed bus traffic.

### 3.2 Data flow

```
human goal → POST /negotiate → Interpreter (qwq:32b) → TaskSpec
   → bus.publish( task_announce {task_id, round 0, source_zone, dest_zone,
                                 constraint, slots[], announce_ts,
                                 bid_deadline_ts, consensus_deadline_ts} )
   → every ActorAgent thread receives it
   → agents publish capability_broadcast + availability_update
   → able + free agents publish bid {slot_id, score}
   → at bid_deadline_ts every agent runs leaderless evaluation on the public bid set
        winner-of-self → publish accept {slot_id}    ;  loser → publish withdraw
   → every agent observes "all slots accepted, stable" → local state = converged
        (any agent may emit observational consensus_reached — a marker, not authority)
   → each accept-holding agent executes its own leg (AGV agent → container; arm agent → animation)
   → any fault → withdraw → that slot reopened at round+1 → re-bid → re-converge
```

No component assigns actors, owns consensus, or dictates order. The **chain**
(`pickup-arm → transport-AGV → place-arm`) is not a planner decision — the *slots* are
derived from the task's source/destination zones (physical topology), and *who fills
each slot* emerges from bidding.

---

## 4. The negotiation protocol

### 4.1 Message envelope

Every bus message carries:

```
type                 one of the 11 types
task_id              unique per announced task
negotiation_round    0 at first announce; +1 on every slot reopen
slot_id              which slot this concerns (null for task-wide messages)
sender               actor id, or "interpreter"
bid_version          +1 each time sender re-states for the same slot in the same round
ts                   message timestamp
payload              type-specific fields
```

### 4.2 The 11 message types

| Type | Sender | When | Payload |
|---|---|---|---|
| `task_announce` | Interpreter, or any actor on reopen | task created, or a slot reopened (round +1) | `source_zone, dest_zone, constraint, slots[], announce_ts, bid_deadline_ts, consensus_deadline_ts` |
| `capability_broadcast` | every actor | on `task_announce` / on join | `reachable_zones, position, speed, capacity` |
| `availability_update` | any actor | status change | `status` (free / busy / faulted) |
| `bid` | an able, free actor | after seeing the announce + others' capabilities | `slot_id, score, basis` |
| `counter_offer` | — | **reserved, never emitted in Phase 1** | — |
| `accept` | the actor that evaluates **itself** the winner | at `bid_deadline_ts`, after leaderless evaluation | `slot_id` |
| `reject` | an actor | it cannot satisfy a slot | `slot_id, reason` |
| `busy` | an actor | already executing another task | — |
| `fault` | an actor | it failed | `reason, slot_id?` |
| `withdraw` | an actor | it lost a slot, or can no longer hold one it won | `slot_id, reason` |
| `consensus_reached` | any actor, **observationally** | it *observes* the consensus condition | `round` — marker only, non-authoritative |

### 4.3 Slot derivation

A transport task "move from zone X to zone Y" derives exactly three slots:

```
pickup@X    kind=arm    zone=X
transport   kind=agv    from=X to=Y
place@Y     kind=arm    zone=Y
```

Deriving slot *structure* from physical topology (an arm serves each station, AGVs move
between) is validation, not assignment — it names no actor. *Who* fills each slot is the
negotiation.

### 4.4 Happy-path flow

1. **Announce.** `/negotiate` → Interpreter → `TaskSpec` → `task_announce` (`round 0`)
   with the derived slots and the three shared deadlines.
2. **Capability + availability.** Each agent broadcasts `capability_broadcast` +
   `availability_update`. The participant set is learned from the bus.
3. **Bid.** Each agent tests every slot against its **own** capability profile. Able +
   free → `bid {slot_id, score}`. Unable / busy → `reject` / `busy` / silence.
4. **Leaderless evaluation.** At the shared `bid_deadline_ts`, each agent applies the
   **one public rule** to the **public bid set** for every slot it bid on:
   winner = best score; tie-break = lowest actor id (fully deterministic → every agent
   computes the identical winner). Winner-of-self → `accept`; otherwise → `withdraw`.
5. **Observational consensus.** Consensus is the *condition*: every slot has exactly one
   valid `accept`, no un-withdrawn competing bid, round stable. Each agent observes it
   independently and sets its **own** local state to `converged`. A `consensus_reached`
   message may be emitted but only **reports** the condition — it never **creates** it.
6. **Execute.** Each `accept`-holding agent runs its own leg. Execution-time mutual
   exclusion stays on the `grid_state` lane/station locks.

### 4.5 The public consensus rule

`constraint → rule` mapping, applied identically by every actor:

```
"shortest time"      → lowest time-score bid wins
"shortest distance"  → lowest distance-score bid wins
(no constraint)      → lowest-actor-id among valid bidders wins (deterministic "first")
tie-break (always)   → lowest actor id
```

Deterministic bid scores: AGV transport score = travel distance / speed (time) or travel
distance (distance); arm pickup/place score = reach cost to the station zone.

---

## 5. Protocol invariants

These are non-negotiable and must hold across every code path.

1. **Broadcast-only negotiation.** No actor ever addresses a negotiation message to a
   specific actor. Everything negotiation-related is broadcast on the shared bus. Direct
   actor-to-actor messaging is a Phase 2 concern and only post-consensus.

2. **Observational consensus.** Consensus and round status are *conditions every actor
   independently detects*, never decisions one actor announces. `consensus_reached` is a
   non-authoritative marker.

3. **Validation, not coordination.** `negotiation.py`'s completeness check may verify
   "the slots are well-formed" and "every slot has a valid accept" — it must never pick
   who fills a slot.

4. **Shared absolute deadlines.** `task_announce` carries `announce_ts`,
   `bid_deadline_ts = announce_ts + BID_WINDOW_S`, and
   `consensus_deadline_ts = bid_deadline_ts + CONSENSUS_WINDOW_S`. Actors use these
   shared absolute timestamps, never local relative timers. Windows are seconds-wide so
   all (deterministic, sub-millisecond) bids land well before any deadline.

5. **Idempotency.** Every handler is idempotent: duplicate `accept` / `withdraw` / `bid`
   for the same `(task_id, round, slot_id, sender, bid_version)` is a no-op; repeated
   `fault` from the same sender in the same round is a no-op; repeated
   `consensus_reached` is deduped by `(task_id, round)`; re-entering a held lifecycle
   state is a no-op.

6. **`withdraw` supersedes.** `withdraw {slot_id}` from actor A invalidates both A's
   active `bid` and any prior `accept` by A for that slot. An `accept` superseded by a
   later `withdraw` from the same actor does not count toward consensus. No stale accept
   survives.

7. **No round ownership.** No component owns "starting the next round." Any actor that
   observes an uncovered slot may publish the `round+1` reopen `task_announce`. To make
   concurrent reopen-announces harmless, the reopen announce's `announce_ts` is **derived
   from the triggering `fault` / `withdraw` message's `ts`** (seen identically by all
   actors), not from wall-clock-at-publish — so all duplicate reopen-announces are
   byte-identical → idempotent → no split-brain.

8. **Round status** — each `(task_id, round)` has an *observed* (never owned) status:
   `open` (bidding/evaluation in progress), `converged` (every slot accepted),
   `failed` (consensus deadline passed with a slot uncovered, no higher round),
   `superseded` (a higher round exists). Delayed messages for a `superseded` round are
   dropped; an actor restarting after a fault reads the bus, derives the highest round +
   status, and rejoins the live round.

9. **Monotonic-round invariant.** An actor may never publish, accept, or execute for a
   round lower than the highest round it has observed for that task. Violating messages
   are dropped. This is the master rule that makes staleness handling airtight.

10. **Execution barrier.** An actor may begin its leg only when all hold: (a) it locally
    observes `converged`, (b) its own slot's `accept` is still valid, (c) no higher round
    exists for the task. If a higher round appears mid-execution it re-checks — slot
    reopened → halt/withdraw; slot still stands → continue.

---

## 6. Failure / renegotiation

**Case A — actor faults during negotiation (before consensus).** It publishes `fault` +
`withdraw`. If the fault is before `bid_deadline_ts`, evaluation at the deadline simply
excludes the withdrawn bid — no reopen. If it had already won a slot → Case B.

**Case B — a won/accepted slot loses its holder (fault, or withdraw).** The slot's
`accept` is invalidated → slot uncovered. Any actor observing this publishes the
`round+1` reopen `task_announce` **scoped to that slot only** (other slots' accepts
stand). Capable free actors re-bid at `round+1`; leaderless evaluation runs again; a new
`accept` converges. Actors already executing other slots **hold at their `grid_state`
lock points** until the reopened slot reconverges — full mid-execution peer recovery is
Phase 2.

**Case C — no convergence.** If `consensus_deadline_ts` passes with a slot uncovered
(no bids, or all bidders faulted), the round's observed status becomes `failed` and the
task is reported **not completable now** — satisfying `goal.md` objective 8. Surfaced on
the bus and UI.

---

## 7. Actor model

### 7.1 Capability profile

Each agent owns a static-ish profile and broadcasts it — there is no central capability
registry:

```
id, kind (arm|agv), reachable_zones[], position, speed, capacity, status
```

Sourced from `pipeline.yaml`: AGV profiles from `warehouse.agvs` + live `agv_state`;
arm profiles from `arms` + `warehouse.stations` (`served_by`).

### 7.2 Lifecycle (per actor, per task)

```
idle ──(task_announce, can fill a slot)──▶ bidding
bidding ──(bid_deadline: won self-eval)──▶ won ──▶ executing ──(leg done)──▶ idle
bidding ──(bid_deadline: lost)───────────▶ idle
any state ──(own failure)──▶ faulted ──(recover)──▶ idle
executing another task ──▶ answers `busy`, ignores announces
(round+1 reopen of a slot it can fill) ──▶ bidding
```

Each agent runs on its own thread, owns its own state, participation, failure,
withdrawal, and re-entry. No external component drives an agent's state.

---

## 8. Frontend (deliberately minimal — Phase 1 is not UI polishing)

- `BusPane` renders **real typed bus messages** streamed from the bus's SocketIO mirror —
  each shows `type, task_id, round, slot, sender`, as a plain round/slot timeline:
  bids → accepts → withdraws → consensus → failures.
- **Delete** `classifyBus` (the regex log-relabeler) in `contract.ts` and the mock-bus
  fiction in `mockData.ts`. The bus panel stops being relabeled logs and becomes the
  real protocol stream (audit M2 closed).
- Mock-only `runtimeSlice` fields (`contracts`, `bid`, `winning`) are dropped or
  repointed to real bus state.
- **No canvas / authoring-tool rework** this phase.

---

## 9. Testing

Deterministic negotiation makes the system genuinely testable (unlike the old LLM path).

**Unit:** bus (pub/sub, staleness drop, idempotency, `bid_version` ordering);
`negotiation.py` (public rule per constraint, tie-break, completeness check); each agent
(bid computation, self-evaluation win/lose, withdraw).

**Integration:** announce → assert consensus + correct winner per constraint
(shortest-time → fastest AGV, nearest → closest, unconstrained → first);
fault-before-consensus → still converges; fault-after-winning → slot reopens at
`round+1`, reconverges to a new winner; no viable bid → `failed`, task reported
not-completable; duplicate `accept`/`withdraw`/`fault` → no state corruption;
stale lower-round message → dropped.

These replace `test_central_brain.py`.

---

## 10. Scope boundaries

**IN scope:** the typed bus, 5 in-process agents, the 11-message protocol, deterministic
bids, leaderless consensus, slot-scoped renegotiation, the honest bus panel, the test
suite.

**OUT of scope (explicit):**

- No LLM in the negotiation loop — the Interpreter is the only AI component.
- No pathfinding or collision avoidance (`PHASE_1_PROMPT.md` §8).
- No real peer-to-peer execution messaging — execution coordination stays on the
  `grid_state` locks; direct peer messaging is Phase 2.
- No `counter_offer` emission — reserved/disabled.
- No actor quarantine / instability suppression (repeated fault/withdraw/oscillation
  damping) — explicit Phase 2+ work.
- No multi-input / multi-output topologies — one source, one destination.
- No canvas / authoring-UI rework.
- AGV-as-compose-service (audit H2) is **not** fixed here — the agent layer commands
  whatever AGV container exists.
- No new containers — arms stay in-process agents; AGV containers are unchanged.

---

## 11. Final deliverable

Per `PHASE_1_PROMPT.md` §10, after implementation produce **`PHASE_1_IMPLEMENTATION_AUDIT.md`**
covering: what centralized authority was removed; how negotiation now works; how actors
participate; how consensus emerges; what remains intentionally central (the Interpreter
only); what is transitional; and what Phase 2 must solve. Include architecture diagrams,
negotiation flow, message examples, the actor lifecycle, and the runtime-ownership model.

---

## 12. Phase 2 forward-pointers

Recorded so Phase 1 does not pre-build them: real peer-to-peer execution messaging
(actors talk directly post-consensus); actor quarantine / convergence-storm damping;
re-enabling `counter_offer` once the base protocol is proven stable; multi-input /
multi-output topologies; resolving AGV container ownership (compose service →
reconciler-owned, audit H2).

---

## 13. Success criteria (from `PHASE_1_PROMPT.md`)

Phase 1 is complete when: no single component assigns actors; actors negotiate through
the bus; capability discovery exists; consensus emerges dynamically; actors can reject
participation; bus communication is real (not relabeled logs or mock fiction);
centralized orchestration authority is removed; and the architecture is directionally
truthful — a primitive but real decentralized system.
