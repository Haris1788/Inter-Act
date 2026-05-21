# Phase 2 — Actor Negotiation, Failure, Replan

## Why this exists

User Story #29 asks for a message bus that all actors can use to (1) receive
tasks from the Interpreter, (2) negotiate to solve them, and (3) report
failure or completion. Phase 2 is the conversation protocol that rides on
top of Phase 1's transport. The negotiation result drives the actors'
existing peer-to-peer execution channel; the bus itself stays lightweight
and carries no execution traffic.

## Architectural rule

```
┌────────────────────────────┬───────────────────────────────────┐
│ MESSAGE BUS                │ Negotiation only                  │
│                            │   TASK_CREATED                    │
│                            │   PATH_PROPOSED                   │
│                            │   PATH_ACCEPTED / PATH_REJECTED   │
│                            │   ACTOR_BUSY / ACTOR_FREE         │
│                            │   ACTOR_FAILED                    │
│                            │   REPLAN_REQUIRED                 │
│                            │   TASK_COMPLETED                  │
├────────────────────────────┼───────────────────────────────────┤
│ DIRECT HTTP                │ Execution / coordination          │
│ (actor → backend → actor)  │   /assign, /set_angle, /move      │
│                            │   /load_ready, /unload_ready      │
│                            │   /station/acquire, /release      │
└────────────────────────────┴───────────────────────────────────┘
```

If a topic ever needs to carry position updates, signal polling, or movement
commands — STOP. That goes on direct HTTP, not the bus. The bus is for
negotiation, lifecycle, and lifecycle alone.

## Event taxonomy

Single source of truth: `scripts/events.py` (mirrored at `app/agents/events.py`
which is a re-export so the backend `from app.agents.events import …`
continues to work and actor containers — which only get `scripts/` — can
`from events import …` too).

```python
ACTOR_ONLINE         # actor boots and registers itself
ACTOR_OFFLINE        # clean shutdown
CAPABILITY_DECLARED  # actor advertises bay / reach / role / status
TASK_CREATED         # Interpreter announces a goal
TASK_AVAILABILITY    # actor → "I can / cannot participate"
PATH_PROPOSED        # Interpreter → here's a proposed path
PATH_ACCEPTED        # actor → "yes I'll do my part"
PATH_REJECTED        # actor → "no, with reason"
ACTOR_BUSY           # actor has accepted a path
ACTOR_FREE           # actor released
ACTOR_FAILED         # actor reports a fault
REPLAN_REQUIRED      # task_tracker → "replan this task, exclude these actors"
TASK_COMPLETED       # final actor on path reports done
```

Envelope: `{type, actorId, ts, msgId, payload, taskId?, correlationId?}`.

## Happy path

```
user                backend                  actors
 │                     │                       │
 │ POST /bus/task/dispatch                     │
 ├────────────────────►│                       │
 │                     │ publish TASK_CREATED  │
 │                     │ (bus, all subscribers)│
 │                     │                       │
 │                     │ HTTP /evaluate_task   │
 │                     ├──────────────────────►│
 │                     │ bus: TASK_AVAILABILITY│
 │                     │◄──────────────────────┤
 │                     │   (1.5s collect)      │
 │                     │                       │
 │                     │ publish PATH_PROPOSED │
 │                     │ HTTP /propose_path    │
 │                     ├──────────────────────►│
 │                     │ bus: PATH_ACCEPTED    │
 │                     │ bus: ACTOR_BUSY       │
 │                     │◄──────────────────────┤
 │                     │   (1.5s collect)      │
 │                     │                       │
 │                     │ HTTP /assign          │
 │                     ├──────────────────────►│  AGV brain runs
 │ {status:"agreed"}   │                       │  steps: acquire,
 │◄────────────────────┤                       │  move, load,
 │                     │                       │  traverse, …
 │                     │                       │
 │                     │ bus: TASK_COMPLETED   │
 │                     │◄──────────────────────┤
 │                     │ task_tracker fans     │
 │                     │ ACTOR_FREE to every   │
 │                     │ participant           │
```

## Failure + replan path

```
   …after PATH_ACCEPTED on path [arm-1, agv-2, arm-2] …
   agv-2's brain crashes mid-route
   │
   │  bus: ACTOR_FAILED {actorId: agv-2, taskId: t-1}
   │      ┌───────────────────────────────────────────┐
   │      │ actor_registry: status → FAILED           │
   │      │ task_tracker: agv-2 was in path of t-1 →  │
   │      │   excluded += {agv-2}, attempts += 1      │
   │      │ if attempts > 3: status = aborted, stop   │
   │      │ else: publish REPLAN_REQUIRED              │
   │      └───────────────────────────────────────────┘
   │
   │  bus: REPLAN_REQUIRED {failedActor: agv-2,
   │                        excluded: [agv-2],
   │                        attempts: 1, task: {…}}
   │      ┌───────────────────────────────────────────┐
   │      │ replan_handler:                            │
   │      │  1. HTTP /release arm-1, arm-2 (survivors) │
   │      │  2. publish ACTOR_FREE for both            │
   │      │  3. call _run_dispatch_round with          │
   │      │     excluded={agv-2}                       │
   │      │  4. new PATH_PROPOSED → arm-1, agv-3, arm-2│
   │      │  5. task_tracker: status → running         │
   │      └───────────────────────────────────────────┘
```

Cap on retries: `task_tracker.MAX_REPLAN_ATTEMPTS = 3`. Once exhausted the
task moves to `aborted` and no further REPLAN_REQUIRED is emitted.

## File map

| File | Responsibility |
|------|----------------|
| `scripts/events.py` | Event constants + envelope builder (single source of truth) |
| `app/agents/events.py` | Backend re-export of the above |
| `scripts/bus_client.py` | Actor-side HTTP `publish()` (Phase 1 adds SSE subscribe) |
| `scripts/agv_server.py` | `_announce_self`, `/evaluate_task`, `/propose_path`, `/assign` brain hook, `/fault`, `/release` |
| `scripts/arm_server.py` | Same surface as AGV, role-aware capability declaration |
| `app/routes/dispatch_routes.py` | `_run_dispatch_round()`, `/bus/task/dispatch`, admin: `/bus/actor/*/free`, `/bus/actor/*/fault`, `/bus/task/<id>`, `/bus/tasks`, `/bus/roster`, `/bus/publish` |
| `app/services/actor_registry.py` | Mirrors roster (ONLINE → CAPABILITY → BUSY/FREE/FAILED) |
| `app/services/task_availability.py` | Collects TASK_AVAILABILITY for Step 3 |
| `app/services/path_negotiation.py` | Collects PATH_ACCEPTED/REJECTED for Step 4 |
| `app/services/task_tracker.py` | Step 7-9: registers tasks; ACTOR_FAILED → REPLAN_REQUIRED; TASK_COMPLETED → ACTOR_FREE fan-out |
| `app/services/replan_handler.py` | Step 8: REPLAN_REQUIRED → release survivors → re-dispatch |

## Three-state local machine on each actor

The actor's local task state has three values so negotiation acceptance
doesn't collide with execution start:

```
 idle ──── propose_path accept ───► reserved ──── /assign ───► running
   ▲                                     │                       │
   │                                     │ negotiation fails     │ task completed / faulted
   └────────────────── /release ◄────────┴───────────────────────┘
```

Without the `reserved` state, the busy gate from Step 5 would 409 the
`/assign` call from Step 6 — they would fight over the same flag.

## Admin / test endpoints

```
POST /bus/task/dispatch        # full negotiation pipeline
POST /bus/actor/<id>/free      # admin release
POST /bus/actor/<id>/fault     # simulate fault → triggers replan chain
GET  /bus/task/<id>            # tracker state for a single task
GET  /bus/tasks                # list active tasks
GET  /bus/roster               # all known actors and their status
```

## Tests

`tests/test_phase2_negotiation.py` covers:
- Envelope shape
- Actor registry lifecycle (online → busy → free → failed)
- Availability collection window
- PATH_ACCEPTED / PATH_REJECTED collection
- ACTOR_FAILED on an active task triggers REPLAN_REQUIRED
- Replan attempts are bounded by MAX_REPLAN_ATTEMPTS (task → aborted)
- TASK_COMPLETED releases every path participant
- Stray ACTOR_FAILED on no-such-task is a no-op

Run: `pytest tests/test_phase2_negotiation.py -v`

## Open follow-ups (for the PR description)

1. **Phase 1 SSE** — `scripts/bus_client.subscribe()` is still a stub. Once
   Purvil's `/bus/subscribe` SSE endpoint lands, actors can receive bus
   events directly and we can drop the HTTP fan-out in `_run_dispatch_round`.
2. **LLM-driven path picker** — `_run_dispatch_round` currently picks the
   highest-scored available AGV deterministically. Phase 3 should route
   through `central_brain.make_plan()` to support multi-package plans and
   color-ordering constraints.
3. **Auth/throttle on /bus/actor/\*/fault** — fine for dev, gate before
   prod.
