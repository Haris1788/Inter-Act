# Phase 2 — To-Do List (Implementation Order)

> Work top-to-bottom. Each task unblocks the next.
> Always open session with: "Read PHASE2_OVERVIEW.md. Stay strictly within its scope."

---

## CMD 1 — Base Actor
**File:** `base_actor.py` (new)
**What:** Abstract base class all actors extend.
- `__init__(actor_id, actor_type, bus)` — store identity, connect to bus
- `status` property: `FREE` | `BUSY`
- abstract `capabilities() -> dict` — each actor defines its own
- `on_startup()` — publishes `CAPABILITY_DECLARED` automatically
- `subscribe_to(event_types)` — helper to register multiple topics at once
- NO decision logic here — only shared plumbing

---

## CMD 2 — Actor Registry
**File:** `actor_registry.py` (new)
**What:** Passive store that listens for `CAPABILITY_DECLARED` and records it.
- Subscribes to `CAPABILITY_DECLARED` on the bus
- Stores: `{ actorId: { capabilities, status, last_seen } }`
- Methods: `get_all()`, `get_by_id(actor_id)`, `get_free_actors()`
- NEVER makes decisions — read-only store for Interpreter to query

---

## CMD 3 — AGV Actor
**File:** `agv_actor.py` (new)
**What:** Extends `BaseActor`. Represents a mobile AGV.
- capabilities: `canReach` (list of nodes), `speed`, `capacity`, `status`
- Subscribes to: `TASK_CREATED`, `PATH_PROPOSED`, `ACTOR_FAILED`, `TASK_COMPLETED`
- On `TASK_CREATED`: evaluate locally — can I reach the required nodes? Am I FREE?
- On `PATH_PROPOSED`: if included → publish `PATH_ACCEPTED`, enter `BUSY`
- On `PATH_PROPOSED`: if NOT included → ignore
- On `TASK_COMPLETED`: return to `FREE`
- On `ACTOR_FAILED` (own id): halt, publish status update
- NO routing logic — just responds to proposals

---

## CMD 4 — Arm Actor
**File:** `arm_actor.py` (new)
**What:** Extends `BaseActor`. Represents a robotic arm (input/output).
- capabilities: `position` (node id), `actions` (load/unload), `status`
- Subscribes to: `TASK_CREATED`, `PATH_PROPOSED`, `TASK_COMPLETED`
- On `PATH_PROPOSED`: if included → publish `PATH_ACCEPTED`, enter `BUSY`
- On `TASK_COMPLETED`: return to `FREE`, publish `TASK_COMPLETED` if final actor
- Peer signal: direct method call to AGV when "pickup ready" (NOT through bus)

---

## CMD 5 — Interpreter
**File:** `interpreter.py` (new)
**What:** Global reasoner. Uses registry to propose plans.
- Subscribes to: `CAPABILITY_DECLARED`, `PATH_ACCEPTED`, `PATH_REJECTED`, `ACTOR_FAILED`, `REPLAN_REQUIRED`, `TASK_COMPLETED`
- `create_task(task_spec)` → publishes `TASK_CREATED`
- On enough `CAPABILITY_DECLARED` received → propose path
- `propose_path(task_id, path)` → publishes `PATH_PROPOSED`
- On `PATH_ACCEPTED` from all required actors → trigger peer execution start
- On `ACTOR_FAILED` → publish `REPLAN_REQUIRED`
- On `REPLAN_REQUIRED` → query registry for FREE replacement → publish new `PATH_PROPOSED`
- NEVER executes steps directly — only publishes events

---

## CMD 6 — Happy Path Integration Test
**File:** `tests/test_negotiation.py` (new)
**What:** End-to-end happy path using NullBus.
- Spin up: 1 Interpreter, 2 AGVs, 2 Arms, 1 Registry
- Interpreter creates task
- Verify: `CAPABILITY_DECLARED` received by registry
- Verify: `PATH_PROPOSED` published
- Verify: `PATH_ACCEPTED` from correct actors
- Verify: selected actors enter `BUSY`
- Verify: non-selected actors stay `FREE`
- Verify: `TASK_COMPLETED` resets all to `FREE`
- Update PHASE2_CHECKLIST.md steps 1–6

---

## CMD 7 — Failure + Replan Test
**File:** `tests/test_negotiation.py` (extend)
**What:** Failure workflow test.
- Same setup as CMD 6
- After `PATH_ACCEPTED`: simulate AGV failure → publish `ACTOR_FAILED`
- Verify: `REPLAN_REQUIRED` published
- Verify: Interpreter proposes new path with replacement AGV
- Verify: failed AGV NOT in new path
- Verify: unrelated tasks/actors unaffected (use task scoping)
- Verify: `TASK_COMPLETED` eventually published
- Update PHASE2_CHECKLIST.md steps 7–9 + exit criteria

---

## CMD 8 — Final Audit
**What:** Scan all new files for architectural violations.
- No centralized step runner
- No hardcoded actor chains
- No global execution manager
- No decision logic in bus
- Actors discover each other ONLY via `CAPABILITY_DECLARED`
- Peer-to-peer signals bypass bus correctly
- Report any violations found
- Update PHASE2_CHECKLIST.md exit criteria
