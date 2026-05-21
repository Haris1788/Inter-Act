# Phase 2 — Actor Negotiation + Failure Workflow

## Goal
Implement how actors and the Interpreter communicate through the message bus to:
- negotiate tasks
- accept roles
- report failures
- trigger replanning

## Scope Boundary (STRICT)

### Actors MUST
- decide locally
- advertise capabilities
- participate in negotiation
- report state changes
- execute peer-to-peer (NOT through the bus)

### Interpreter MUST
- reason globally
- propose plans
- replan when failures occur

### Bus MUST
- only transport communication
- NOT be modified unless absolutely necessary

### NEVER implement
- a central step runner
- a global execution manager
- hardcoded actor chains
- command-based orchestration

---

## Architecture Summary
```
Interpreter ──publishes──► TASK_CREATED ──► Bus ──► Actors (evaluate)
Actors ──publish──► CAPABILITY_DECLARED ──► Bus ──► Interpreter (discovers)
Interpreter ──publishes──► PATH_PROPOSED ──► Bus ──► Actors (accept/reject)
Actors ──publish──► PATH_ACCEPTED ──► Bus ──► Interpreter (confirms)
Actor ──publishes──► ACTOR_FAILED ──► Bus ──► Interpreter (replans)
Interpreter ──publishes──► REPLAN_REQUIRED ──► Bus ──► Actors (re-negotiate)
Final Actor ──publishes──► TASK_COMPLETED ──► Bus ──► All (reset state)

Peer execution: Actor ──direct signal──► Actor (bypasses bus)
```

---

## Existing Foundation (Phase 1 — DO NOT MODIFY)
- `message_bus.py` — pub/sub, wildcard, task scoping, logging
- `event_types.py` — all EventType constants
- `event.py` — unified Event dataclass

## Files To Create
| File | Purpose |
|---|---|
| `base_actor.py` | Abstract base class all actors extend |
| `agv_actor.py` | AGV actor — mobility, routing, busy state |
| `arm_actor.py` | Arm actor — load/unload capability |
| `interpreter.py` | Global reasoner — task creation, path proposal, replanning |
| `actor_registry.py` | Passive registry — tracks declared capabilities |
| `tests/test_negotiation.py` | Negotiation + failure workflow tests |

---

## Actor State Machine
```
FREE → BUSY (on PATH_ACCEPTED)
BUSY → FREE (on TASK_COMPLETED or ACTOR_FAILED)
BUSY → rejects all new TASK_CREATED events
```

## Event Flow (Happy Path)
```
1. CAPABILITY_DECLARED  (each actor → bus → interpreter)
2. TASK_CREATED         (interpreter → bus → actors)
3. PATH_PROPOSED        (interpreter → bus → selected actors)
4. PATH_ACCEPTED        (actors → bus → interpreter)
5. [peer-to-peer exec]  (actor → actor directly)
6. TASK_COMPLETED       (final actor → bus → all)
```

## Event Flow (Failure Path)
```
1–4. same as above
5. ACTOR_FAILED         (failed actor → bus → interpreter)
6. REPLAN_REQUIRED      (bus → interpreter)
7. PATH_PROPOSED        (interpreter proposes new path)
8. PATH_ACCEPTED        (remaining actors accept)
9. TASK_COMPLETED       (final actor → bus → all)
```
