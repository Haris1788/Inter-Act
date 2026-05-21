# Phase 2 — Progress Checklist

## Step 1 — Actor Registration
- [x] Create `base_actor.py` — abstract base class
- [x] Each actor connects to bus on init
- [x] Each actor subscribes to relevant events on startup
- [x] Each actor exposes: `actor_id`, `actor_type`, `status`
- [x] **EXIT: Verified in tests/test_negotiation.py::test_capabilities_declared**

## Step 2 — Capability Broadcasting
- [x] Each actor publishes `CAPABILITY_DECLARED` on startup
- [x] AGV capability includes: `canReach`, `speed`, `capacity`, `status`
- [x] Arm capability includes: `position`, `actions`, `status`
- [x] Actors do NOT hardcode knowledge of other actors
- [x] Create `actor_registry.py` — passively collects `CAPABILITY_DECLARED` events
- [x] Registry stores capabilities by `actorId`
- [x] **EXIT: Verified in tests/test_negotiation.py::test_registry_populated**

## Step 3 — Task Reception
- [x] All actors subscribe to `TASK_CREATED` (AGV implemented)
- [x] Each actor evaluates locally: can I participate? (AGV implemented)
- [x] Busy actors auto-reject (no publish, silent ignore) (AGV implemented)
- [x] Free actors publish availability response (awaits Interpreter)
- [x] **EXIT: Verified in tests/test_negotiation.py::test_task_creation**

## Step 4 — Negotiation Workflow
- [x] Interpreter publishes `TASK_CREATED`
- [x] Interpreter listens for actor availability
- [x] Interpreter publishes `PATH_PROPOSED` with selected actors + route
- [x] Actors subscribe to `PATH_PROPOSED`
- [x] Actors publish `PATH_ACCEPTED` or `PATH_REJECTED`
- [x] Interpreter confirms on `PATH_ACCEPTED`
- [x] **EXIT: Verified in tests/test_negotiation.py::test_path_proposal**

## Step 5 — Busy State Handling
- [x] Actor enters `BUSY` on `PATH_ACCEPTED`
- [x] Actor rejects new tasks while `BUSY`
- [x] Non-selected actors remain `FREE`
- [x] Multiple tasks can run in parallel (different actors)
- [x] **EXIT: Verified in tests/test_negotiation.py::test_path_acceptance_and_busy_state**

## Step 6 — Peer-to-Peer Execution Trigger
- [x] After `PATH_ACCEPTED` actors signal each other directly
- [x] Peer signals do NOT go through the message bus
- [x] Example: Arm-1 signals AGV-3 "pickup ready" directly
- [x] Bus remains lightweight during execution phase
- [x] **EXIT: Verified in tests/test_negotiation.py::test_peer_to_peer_signaling**

## Step 7 — Failure Handling
- [ ] Failed actor publishes `ACTOR_FAILED` with `actorId` + `taskId`
- [ ] Affected actors halt execution on `ACTOR_FAILED`
- [ ] Unrelated tasks/actors continue normally (task scoping)
- [ ] Bus does NOT handle the failure — only broadcasts it

## Step 8 — Replanning Trigger
- [ ] `REPLAN_REQUIRED` published when failure invalidates path
- [ ] Interpreter subscribes to `REPLAN_REQUIRED`
- [ ] Interpreter generates new plan (different actor, same task)
- [ ] New `PATH_PROPOSED` published with replacement actor
- [ ] Old failed actor NOT included in new plan

## Step 9 — Task Completion
- [ ] Final actor publishes `TASK_COMPLETED` with `taskId`
- [ ] All participating actors return to `FREE` on `TASK_COMPLETED`
- [ ] Registry updates actor status to `FREE`
- [ ] Bus broadcasts completion to all subscribers

---

## Phase 2 Exit Criteria
All boxes above checked AND:
- [ ] Full happy path demo works end-to-end
- [ ] Failure + replan demo works end-to-end
- [ ] No centralized execution logic anywhere
- [ ] Actors discover each other only via `CAPABILITY_DECLARED`
- [ ] Peer-to-peer execution bypasses bus correctly
- [ ] All tests pass in `tests/test_negotiation.py`
