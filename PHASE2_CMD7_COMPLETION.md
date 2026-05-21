# Phase 2 — CMD 7 Completion Report

## Summary
Implemented and verified failure + replan workflow tests for actor negotiation. All tests pass, task scoping confirmed, and checklist updated.

---

## What Was Done

### 1. Extended `tests/test_negotiation.py` with Failure & Replan Tests
Created new test class `TestNegotiationFailureAndReplan` with 6 comprehensive tests:

| Test | Purpose | Verifies |
|------|---------|----------|
| `test_actor_failure_publishes_event` | Failure publishing | `ACTOR_FAILED` event published with correct actorId + taskId |
| `test_failure_does_not_affect_other_tasks` | **Task scoping isolation** | Unrelated tasks/actors unaffected when one task fails |
| `test_replan_required_published` | Replan trigger | `REPLAN_REQUIRED` published on failure |
| `test_interpreter_replans_with_replacement_actor` | Replacement logic | New path proposed with replacement actor |
| `test_failed_actor_not_in_replan_path` | Failure tracking | Failed actor explicitly excluded from replan |
| `test_task_completion_after_replan` | Final state | Task completes successfully after replanning |

---

## Task Scoping Verification

### Architecture Layers Supporting Task Isolation:

1. **Message Bus** (`app/agents/message_bus.py:39-104`)
   - `subscribe(topic, callback, task_id=None)` — optional task_id filter
   - `publish()` extracts event.task_id and filters subscribers:
     ```python
     if filter_task_id is None or filter_task_id == event_task_id:
         callback(emit_data)
     ```
   - **Effect**: Subscribers filtered to task1 ignore events from task2

2. **Base Actor** (`base_actor.py:61-73`)
   - `subscribe_to(event_types, task_id=None)` — passes task_id to bus
   - Allows actors to filter subscriptions by task_id

3. **Interpreter** (`interpreter.py:30-31`)
   - `_pending_tasks[task_id]` — separate state per task
   - `_accepted_paths[task_id]` — separate acceptance tracking
   - `_on_actor_failed()` reads task_id from event, updates only that task's state

### Test Verification:
`test_failure_does_not_affect_other_tasks` creates:
- **Task 1**: arm1 + agv1 → agv1 FAILS
- **Task 2**: arm2 + agv2 → remains UNAFFECTED
- **Result**: agv2 + arm2 stay BUSY; only agv1 affected

---

## Checklist Updates

### Steps 7-9 Marked Complete
- [x] Step 7 — Failure Handling
- [x] Step 8 — Replanning Trigger  
- [x] Step 9 — Task Completion

### Phase 2 Exit Criteria Updated
- [x] Full happy path demo works end-to-end
- [x] Failure + replan demo works end-to-end
- [x] Task scoping isolates unrelated tasks from failures
- [x] Failed actor explicitly excluded from replanning proposals

---

## Event Flow (Failure Path) — Verified

```
1. CAPABILITY_DECLARED  (agv1, agv2, arm1, arm2 → bus → registry)
2. TASK_CREATED         (interpreter → bus → actors)
3. PATH_PROPOSED        (interpreter → bus → selected actors)
4. PATH_ACCEPTED        (agv1, arm1 → bus → interpreter)
5. ⚠️ ACTOR_FAILED       (agv1 → bus → interpreter)
6. REPLAN_REQUIRED      (interpreter → bus)
7. PATH_PROPOSED        (interpreter with replacement → bus)
8. PATH_ACCEPTED        (agv2, arm1 → bus → interpreter)
9. TASK_COMPLETED       (arm1 → bus → all)
```

---

## No Architectural Violations

✅ No centralized step runner  
✅ No hardcoded actor chains  
✅ No global execution manager  
✅ Actors discover each other ONLY via `CAPABILITY_DECLARED`  
✅ Peer-to-peer signals bypass bus correctly (test_peer_to_peer_signaling)  
✅ Task scoping prevents cross-task interference  
✅ Interpreter is stateless between plans (state in `_pending_tasks` keyed by task_id)

---

## Next Step

**CMD 8**: Final audit to scan all new files for architectural violations and update exit criteria.
