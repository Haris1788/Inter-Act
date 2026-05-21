# Phase 1 — Message Bus Infrastructure

## Goal
Build the shared communication layer for all actors and system components.

## Scope Boundary (STRICT)
The bus does ONE thing: **transport messages**.

### Bus MUST NOT
- make decisions
- choose actors
- calculate routes
- orchestrate execution
- react to failures
- solve replanning

### Bus MUST
- register subscribers
- unsubscribe listeners
- publish events
- broadcast to all matching listeners
- log every event (timestamp + type + taskId)
- scope events by taskId
- support wildcard (all-events) subscription

---

## Existing File
`message_bus.py` — base `MessageBus`, `SocketIOBus`, `NullBus`, singleton `get_bus()`/`set_bus()`

## Files To Create
| File | Purpose |
|---|---|
| `event_types.py` | `EventType` enum — all standard event name constants |
| `event.py` | `Event` dataclass — unified message structure |
| `message_bus.py` | Extend with logging, wildcard subs, task scoping, unsubscribe |

---

## Required Event Types (MVP)
```
TASK_CREATED
CAPABILITY_DECLARED
NEGOTIATION_STARTED
PATH_PROPOSED
PATH_ACCEPTED
ACTOR_BUSY
ACTOR_FAILED
PATH_INVALIDATED
REPLAN_REQUIRED
TASK_COMPLETED
```

## Required Event Structure
```json
{
  "id": "msg-001",
  "timestamp": "2026-05-21T10:00:00Z",
  "type": "TASK_CREATED",
  "sender": "Interpreter",
  "taskId": "task-01",
  "payload": {}
}
```

---

## Architectural Rule
> If the bus starts making decisions → architecture becomes centralized again. Avoid this at all costs.
