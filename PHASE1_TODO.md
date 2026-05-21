# Phase 1 — To-Do List (Implementation Order)

> Work top-to-bottom. Each task unblocks the next.

---

## 1. Fix `get_bus()` thread safety
**File:** `message_bus.py`
**What:** Add double-checked locking so two threads can't both create `SocketIOBus`.
```python
_init_lock = threading.Lock()

def get_bus():
    global _bus
    if _bus is None:
        with _init_lock:
            if _bus is None:
                _bus = SocketIOBus()
    return _bus
```

---

## 2. Create `event_types.py`
**File:** `event_types.py` (new)
**What:** `EventType` enum with all MVP constants.
**Events:** TASK_CREATED, CAPABILITY_DECLARED, NEGOTIATION_STARTED, PATH_PROPOSED, PATH_ACCEPTED, ACTOR_BUSY, ACTOR_FAILED, PATH_INVALIDATED, REPLAN_REQUIRED, TASK_COMPLETED

---

## 3. Create `event.py`
**File:** `event.py` (new)
**What:** `Event` dataclass — unified message structure.
**Fields:** id (uuid4), timestamp (ISO), type (EventType), sender (str), taskId (str), payload (dict)
**Rule:** `id` and `timestamp` auto-generated if not provided.

---

## 4. Update `publish()` to accept `Event`
**File:** `message_bus.py`
**What:** `publish(event: Event)` instead of `publish(topic, data)`.
**Keep:** backward-compat `publish(topic, data)` wrapper if other code already uses it.

---

## 5. Add wildcard subscription
**File:** `message_bus.py`
**What:** `subscribe("*", callback)` receives every published event.
**Use case:** Logger, UI monitor, debug tools.

---

## 6. Add message logging
**File:** `message_bus.py`
**What:** 
- Add `import logging` + `log = logging.getLogger(__name__)`
- Log every event on publish: `log.info("[BUS] %s | task=%s | from=%s", event.type, event.taskId, event.sender)`
- Replace all `except Exception: pass` with `log.warning(...)`
- Emit copy to SocketIO channel `event_log` for UI visibility

---

## 7. Add task scoping to subscriptions
**File:** `message_bus.py`
**What:** `subscribe(event_type, callback, task_id=None)`
- If `task_id` given → callback only fires for that task's events
- If `task_id=None` → fires for all tasks (current behavior)

---

## 8. Verify failure + replan event flow
**What:** Manual check — no code in bus reacts to `ACTOR_FAILED` or `REPLAN_REQUIRED`.
Bus only broadcasts. Confirm no `if event.type == ACTOR_FAILED` logic anywhere in `message_bus.py`.

---

## 9. Write unit tests
**File:** `tests/test_message_bus.py` (new)
**Cases:**
- publish → subscriber receives event
- wildcard subscriber receives all events
- task-scoped subscriber does NOT receive other task's events
- unsubscribe → callback no longer fires
- NullBus works without Flask context
- ACTOR_FAILED broadcasts to all subscribers, bus takes no action
