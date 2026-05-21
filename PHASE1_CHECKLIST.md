# Phase 1 — Progress Checklist

## Step 1 — Message Bus Core
- [x] `subscribe(topic, callback)`
- [x] `publish(topic, data)`
- [x] `unsubscribe(topic, callback)`
- [x] Thread-safe `_subs` with `_lock`
- [x] `SocketIOBus` — real emit via Flask socketio
- [x] `NullBus` — offline/test bus
- [x] `get_bus()` / `set_bus()` singleton
- [x] Thread-safe `get_bus()` (double-checked locking)
- [x] Wildcard subscription — listen to ALL events

## Step 2 — Unified Event Structure
- [x] Create `event.py`
- [x] `Event` dataclass with: `id`, `timestamp`, `type`, `sender`, `taskId`, `payload`
- [x] Auto-generate `id` (uuid) and `timestamp` on construction
- [x] `publish()` accepts `Event` not raw `dict` (backward-compat overload for (str, dict))

## Step 3 — Event Type Constants
- [x] Create `event_types.py`
- [x] `EventType` enum with all MVP events:
  - [x] `TASK_CREATED`
  - [x] `CAPABILITY_DECLARED`
  - [x] `NEGOTIATION_STARTED`
  - [x] `PATH_PROPOSED`
  - [x] `PATH_ACCEPTED`
  - [x] `ACTOR_BUSY`
  - [x] `ACTOR_FAILED`
  - [x] `PATH_INVALIDATED`
  - [x] `REPLAN_REQUIRED`
  - [x] `TASK_COMPLETED`

## Step 4 — Topic Routing
- [x] Subscribe to specific topic
- [x] Wildcard subscriber (`*`) — receives every event

## Step 5 — Message Logging
- [x] Every published event logged with: timestamp, type, taskId, sender
- [x] Replace all `except Exception: pass` with `log.warning(...)`
- [x] Log visible in console
- [x] Log observable for UI (emit to `event_log` SocketIO channel)

## Step 6 — Task Scoping
- [x] `subscribe()` accepts optional `task_id` parameter
- [x] If `task_id=None`, callback fires for all tasks (existing behavior unchanged)
- [x] If `task_id` given, callback only fires when `event.taskId` matches
- [x] Events from `task-001` do NOT trigger `task-002` subscribers

## Step 7 — Failure Broadcast
- [x] `ACTOR_FAILED` event structure defined
- [x] Bus broadcasts failure to all subscribers — does NOT handle it
- [x] Verified: no decision logic in bus on failure

## Step 8 — Replan Escalation
- [x] `REPLAN_REQUIRED` event structure defined
- [x] Bus emits `REPLAN_REQUIRED` when triggered — does NOT calculate new plan
- [x] Interpreter subscribes to this event (out of scope for Phase 1)

---

## Phase 1 Exit Criteria
All boxes above checked AND:
- [x] Bus has zero decision/orchestration logic
- [x] All events follow unified `Event` structure
- [x] Message log visible in console/UI
- [x] `NullBus` passes all unit tests without Flask

### Test Coverage
✅ **23/23 tests pass** in `tests/test_message_bus.py`:
- **Pub/Sub** (3 tests): publish → subscriber receives correct Event; topic filtering; multiple subscribers
- **Wildcard** (2 tests): wildcard subscriber receives all events; coexistence with specific subscriptions
- **Task Scoping** (3 tests): task-scoped subscriber ignores other task's events; unscoped receives all; wildcard with scoping
- **Unsubscribe** (4 tests): removes callback; only removes specific callback; safe on nonexistent topic/callback
- **NullBus** (2 tests): works without Flask context; no SocketIO emit attempted
- **ACTOR_FAILED** (3 tests): broadcasts to all subscribers; zero handling in bus; respects task scoping
- **Thread Safety** (2 tests): concurrent subscriptions; concurrent publish/subscribe
- **Backward Compatibility** (2 tests): string/dict publish; error on missing data
- **Event Metadata** (2 tests): auto-generated id and timestamp; unique ids per event
