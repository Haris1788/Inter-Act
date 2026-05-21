# INTER.act — System Architecture

**Project:** Autonomous Multi-Agent Robotic Coordination via Agentic AI
**Stakeholders:** IN3 Group, AiSuNe Foundation, Ostfalia University (SoSe 2026)
**Document Version:** 2.0
**Date:** 02 April 2026

---

## 1. What this system is

A browser-based 2D simulation where a configurable number of robotic arms pass objects through a production pipeline. The arm count, layout, dependencies, and failure behavior are controlled by a single YAML config file. Each arm runs in its own Docker container with health monitoring. If one arm goes down, downstream arms stop automatically.

The project has three phases. Phase 1 (what's built now) is scripted arm control with full containerization and failure propagation. Phase 2 (next) adds LLM-planned movements. Phase 3 (later) adds multi-agent peer-to-peer coordination.

---

## 2. What's built (Phase 1 — current state)

### 2.1 Container architecture

The system runs as 6+ containers. The number scales with the arm count in `pipeline.yaml`.

```
┌──────────┐     ┌─────────┐     ┌─────────────┐
│ frontend │────>│ backend │<────│ demo-runner  │
│ nginx:80 │     │ :5000   │     │ :6000        │
└──────────┘     └────┬────┘     └──────────────┘
                      │ HTTP
         ┌────────────┼────────────┐
         │            │            │
      ┌──┴──┐     ┌──┴──┐     ┌──┴──┐
      │arm-1│     │arm-2│ ... │arm-N│
      │:8001│     │:8002│     │:800N│
      └─────┘     └─────┘     └─────┘
```

| Container | What it does |
|-----------|-------------|
| backend (Flask :5000) | API server + SocketIO broadcast. Pure API, no pipeline logic. Runs health monitor for arm containers. |
| frontend (nginx :8080) | Static file serving, proxies API and WebSocket to backend |
| demo-runner (Flask :6000) | Executes pipeline via HTTP to backend. Separate container to avoid thread starvation. |
| arm-1..N (Flask :8001..N) | Independent microservices. Each knows its own ARM_ID, forwards commands to backend. |

`scripts/generate_compose.py` reads `pipeline.yaml` and writes `docker-compose.yml` with the correct number of arm services.

### 2.2 Config-driven design

`pipeline.yaml` is the single source of truth:

```yaml
pipeline:
  mode: dependency          # sequential | parallel | dependency
  products: 3
  speed: normal

failure_policy:
  default: isolate          # stop | skip | retry | isolate
  retry_max: 3
  retry_delay: 2.0

health:
  check_interval: 2.0       # seconds between health pings
  timeout: 5.0
  max_missed: 3              # missed pings before marking dead

arms:
  - id: 1
    label: "Arm 1"
    color: "#e74c3c"
    depends_on: []
    critical: true
  - id: 2
    depends_on: [1]
    critical: true
  # add more arms — no code changes needed
```

The config loader (`app/models/pipeline_config.py`) reads this file and generates:
- Scene layout: arm positions auto-computed from arm count (`spacing = 900 / N`)
- Handoff zones: midpoints between adjacent arms, sized proportionally
- Arm state dict: one entry per arm at 90 degrees
- Handoff state names: `handoff_1` through `handoff_{N-1}`
- Dependency graph: validated for cycles and missing references using Kahn's algorithm
- Topological execution order

Adding an arm: add 6 lines to the YAML, run `python scripts/generate_compose.py`, rebuild Docker.

### 2.3 Pipeline modes

**sequential** — arms chain linearly. N-1 transfer points. Arm order follows the config list.

**parallel** — each arm works independently. No transfer points.

**dependency** — arms chain based on the `depends_on` field. For linear chains this is identical to sequential. For branching graphs, each dependency edge gets its own transfer point. The pipeline builder runs a topological sort to determine execution order.

### 2.4 Dependency awareness and failure propagation

Each arm can depend on upstream arms via `depends_on`. When an arm fails or is stopped:

1. Backend health monitor pings each arm container's `/health` endpoint every 2 seconds
2. Three missed pings = arm marked as dead
3. `get_all_downstream(arm_id)` finds all transitively affected arms via BFS
4. Downstream arms are marked as stopped
5. Demo-runner threads detect the change within 2 seconds via `cancel_check` in TransferPoint
6. Downstream arm threads stop themselves

Stopping arm 6 in a 10-arm chain: arms 7, 8, 9, 10 stop within ~6 seconds. Arms 1-5 keep running.

Failure policies (configurable per arm or globally):
- `stop` — halt and propagate downstream
- `skip` — skip the failed product, continue
- `retry` — retry up to N times with delay
- `isolate` — stop this arm, don't propagate

### 2.5 Data flow

```
Demo-runner thread
    |
    | POST /set_angle {arm_id, angle}
    v
Backend (Flask)
    ├─ arm_state.set_angle() [thread-safe, lock-protected]
    ├─ socketio.emit('state_update') [to all browsers]
    ├─ socketio.emit('log_message') [timestamped]
    v
Browser (SocketIO client)
    ├─ applyStateUpdate() [starts arm transition]
    ├─ appendLogEntry() [log panel]
    v
Canvas animation [easeOutCubic, 600ms]
```

Object tracking follows the same pattern: demo-runner POSTs `/object_event`, backend stores and broadcasts, browser renders.

### 2.6 Transfer point synchronization

Arms coordinate through `TransferPoint` objects using `threading.Condition`:

- `place(obj_id)` — deposits object, wakes downstream arm
- `wait_and_pick(cancel_check)` — blocks until object arrives, checks `cancel_check` every 2 seconds for upstream failure
- `wait_until_empty(cancel_check)` — blocks until cleared, also cancellable

All state reads and writes happen inside the Condition's lock. No gap between checking and acting. The old Event + Lock pattern had a race condition; Condition wraps both into one.

### 2.7 Why the demo-runner is separate

The backend used to run the pipeline itself. The pipeline makes ~50 HTTP requests per demo back to the same Flask process. Werkzeug's thread pool got saturated, blocking SocketIO. Moving it to a separate container fixed it — HTTP requests arrive as normal external traffic.

### 2.8 Proportional UI scaling

Canvas dimensions and all drawing sizes scale with arm count:
- Arm spacing: `900 / N` pixels
- Arm length: `min(150, spacing * 0.55)` pixels
- Scale factor in JS: `S = 3.5 / N`
- Line widths, radii, fonts, object boxes all multiply by S

3 arms: full-size elements. 10 arms: compact but readable. The frontend reads everything from `/config` at page load — no hardcoded arm count in JS.

### 2.9 Health monitoring

`app/services/health_monitor.py` runs a background thread that pings each arm container:

- Alive: responds to GET `/health`
- Unhealthy: 1-2 missed pings
- Dead: 3+ missed pings → triggers `mark_arm_failed()` → cascades to downstream

No API call needed. Stop a Docker container and the system reacts.

---

## 3. Technology choices

### 3.1 Backend: Flask + Flask-SocketIO

WebSocket is the primary communication channel. State changes push to the browser in real time. REST is secondary — scripts submit target angles, but the real data flow is event-driven.

Flask-SocketIO has been stable since 2014, single-package install, handles reconnection and HTTP long-polling fallback. FastAPI would require manual ASGI mounting for SocketIO. For a university project where team velocity matters, Flask is the right call.

The honest trade-off: if Phase 3 proves Flask is the bottleneck for concurrent LLM calls, migrating to FastAPI means rewriting ~50 lines of route definitions. The business logic modules (arm_state, pipeline_config, services) are framework-agnostic.

### 3.2 Frontend: Vanilla JS + Canvas API

The render target is `<canvas>`. React/Vue optimize DOM diffing, which is irrelevant when every pixel is drawn via `ctx.arc()` and `ctx.lineTo()`. Zero build tooling means zero build failures.

### 3.3 Config: pipeline.yaml + pyyaml

YAML over JSON because it supports comments. Users edit this file by hand. Comments explain what each field does.

### 3.4 Threading: Condition variables

Transfer points use `threading.Condition` instead of Event + Lock. Condition wraps both into one primitive, making state transitions atomic. The `cancel_check` polling (every 2 seconds) lets arms detect upstream failures without waiting for a 60-second timeout.

### 3.5 Docker Compose (not Kubernetes)

University project, single machine, no multi-node need. Compose handles dynamic service count via `generate_compose.py`. Kubernetes would add manifests, services, ingress, RBAC — none needed here. If later required, the YAML-in/YAML-out generator pattern translates to Helm charts.

---

## 4. Module structure

```
D:/Inter.act/
├── pipeline.yaml                    # Source of truth for arm count, mode, policies
├── run.py                           # Entry point: socketio.run(app)
├── Dockerfile                       # Backend container
├── docker-compose.yml               # Generated by generate_compose.py
│
├── app/
│   ├── __init__.py                  # create_app() factory, SocketIO init
│   ├── models/
│   │   ├── pipeline_config.py       # Loads YAML, generates layout, validates DAG
│   │   ├── arm_state.py             # Arm angles + health tracking, lock-protected
│   │   └── scene_config.py          # Delegates to pipeline_config
│   ├── routes/
│   │   ├── arm_routes.py            # 19 endpoints: arm control, objects, health, demo proxy
│   │   └── page_routes.py           # HTML + /health
│   ├── sockets/
│   │   └── events.py                # SocketIO connect/disconnect
│   ├── services/
│   │   ├── health_monitor.py        # Background thread pinging arm containers
│   │   ├── llm_service.py           # Phase 2: LLM API wrapper
│   │   ├── prompt_builder.py        # Phase 2: system prompt from live state
│   │   ├── plan_validator.py        # Phase 2: validates LLM output
│   │   └── plan_executor.py         # Phase 2: executes plans step by step
│   ├── agents/                      # Phase 3: multi-agent coordination
│   │   ├── central_brain.py         # LangGraph orchestrator
│   │   ├── arm_agent.py             # Per-arm LangChain agent
│   │   └── message_bus.py           # Abstract MessageBus + implementations
│   ├── static/
│   │   ├── js/simulation.js         # Canvas renderer, SocketIO client, animation
│   │   └── css/style.css            # Responsive layout, log panel styling
│   └── templates/
│       └── index.html               # Canvas + Run Demo button + log panel
│
├── scripts/
│   ├── run_demo.py                  # Pipeline orchestrator (TransferPoint, ArmController)
│   ├── demo_runner_server.py        # Demo-runner container Flask app
│   ├── arm_server.py                # Arm container Flask app
│   ├── arm_1_script.py              # CLI for arm 1
│   ├── arm_2_script.py              # CLI for arm 2
│   ├── arm_3_script.py              # CLI for arm 3
│   ├── utils.py                     # HTTP helpers (send_command, send_health, etc.)
│   └── generate_compose.py          # Reads pipeline.yaml, writes docker-compose.yml
│
├── docker/
│   ├── Dockerfile.frontend          # nginx:alpine
│   ├── Dockerfile.arm               # python:3.11-slim + flask + requests
│   ├── Dockerfile.demo-runner       # python:3.11-slim + flask + requests + pyyaml
│   └── nginx.conf                   # Reverse proxy: static files + API + WebSocket
│
├── tests/
│   ├── conftest.py                  # Fixtures: app, client, reset_arm_state
│   ├── test_arm_state.py            # State validation, thread safety
│   ├── test_arm_routes.py           # API integration tests
│   └── test_concurrent.py           # 30 concurrent writes, no corruption
│
└── docs/
    ├── architecture.md              # This document
    ├── system_documentation.md      # Full technical reference
    ├── project_concept.md           # What and why
    ├── strategic_plan.md            # Full semester vision
    ├── sprint_plan_A_full.md        # 5-week roadmap
    └── sprint_plan_B_immediate.md   # Current sprint
```

### Why this structure

- `pipeline.yaml` at root because it is the first thing you edit.
- `app/models/pipeline_config.py` is the bridge between YAML and everything else. Scene config, arm state, and routes all delegate to it.
- `scripts/` is outside `app/`. Scripts are HTTP clients. They never import from `app/`. This means they work identically in Docker, locally, or against a remote server. Same pattern real hardware controllers would use.
- `app/services/` has one implemented module (health_monitor) and stubs for Phase 2. Import paths are established now. Phase 2 code drops in without restructuring.
- `app/agents/` is entirely Phase 3. Stubs exist to reserve the directory and import structure.

---

## 5. API surface

19 endpoints on the backend:

| Endpoint | Method | What it does |
|----------|--------|-------------|
| / | GET | Simulation page |
| /health | GET | Container health check |
| /set_angle | POST | Move arm: validates, updates state, emits SocketIO |
| /state | GET | All arm states |
| /state/\<id\> | GET | Single arm |
| /config | GET | Scene layout (dynamic, from pipeline.yaml) |
| /object_event | POST | Track object position in pipeline |
| /objects | GET | All tracked objects |
| /objects/reset | POST | Clear objects |
| /log | POST | Receive log from demo-runner, broadcast to browser |
| /arms/health | GET | All arm health states |
| /arms/\<id\>/health | POST | Report arm health |
| /arms/\<id\>/stop | POST | Stop arm + cascade to downstream |
| /arms/\<id\>/allowed | GET | Can this arm execute? |
| /pipeline/status | GET | Full status: health, DAG validity, topo order |
| /reload_config | POST | Hot-reload pipeline.yaml without restart |
| /demo/start | POST | Start demo (proxied to demo-runner) |
| /demo/status | GET | Is demo running |
| /demo/stop | POST | Stop demo |

Each arm microservice exposes: `/health`, `/move`, `/sequence`.

Demo-runner exposes: `/health`, `/run`, `/status`, `/stop`.

---

## 6. Problems solved during development

| # | Problem | Root cause | Fix |
|---|---------|-----------|-----|
| 1 | Arms collided at handoff zones | Tips converge at same pixel at 0/180 degrees | Signal fires at 0 deg, ACTION_DURATION pause gives clearance |
| 2 | Arms vibrated at handoff zones | Exponential lerp never reaches target | Time-based easeOutCubic, 600ms completion |
| 3 | Objects teleported on pickup | SocketIO event arrived before animation finished | 200ms easing bridges network delay |
| 4 | Objects followed arm during retract | Still in "carried" state during retract | Detach before retract |
| 5 | Transfer point lost signals | Event + Lock gap | Single Condition variable |
| 6 | CPU burned during polling | Busy-wait at 20Hz | Condition blocks at OS level |
| 7 | Object state corrupted | No lock on dict | Added threading.Lock |
| 8 | Arm threads crashed silently | Exception swallowed | try/except wrapper |
| 9 | Arms idle between handoffs | Signal after retract | Signal before retract, parallel operation |
| 10 | Objects overflowed zones | Fixed 24px spacing | Dynamic compression |
| 11 | Canvas frozen in Docker | HTTP self-calls starved thread pool | Demo-runner in separate container |
| 12 | Downstream kept running after upstream stopped | No dependency awareness | Health monitor + cancel_check every 2s |

---

## 7. Future: Phase 2 — LLM-planned control

### 7.1 What it adds

A text input in the browser. The user types "Move the object from intake to output." An LLM plans the arm movements, Python validates the plan, and the arms execute.

### 7.2 The LLM's role

The LLM is a spatial planner, not a real-time controller. It receives arm positions, reach zones, angle rules, and a task. It returns a JSON array of movement steps. Python validates every step before execution.

### 7.3 Why system prompt injection, not RAG or fine-tuning

The entire knowledge base is ~40 lines of arm config. It fits in a system prompt with room to spare. RAG would embed, store, retrieve, and inject the same 40 lines every call — identical to putting them in the prompt but with added latency and infrastructure.

Fine-tuning would require hundreds of hand-crafted training pairs and re-training when arm config changes. A system prompt plus a Python validator achieves the same result with zero training infrastructure.

### 7.4 Prompt architecture

```
SYSTEM PROMPT = STATIC_CONFIG + CURRENT_STATE + TASK_INSTRUCTIONS
```

STATIC_CONFIG: arm positions, angle rules (0=right, 90=up, 180=left), handoff zones, collision rules. Loaded from pipeline_config at call time.

CURRENT_STATE: current angle and status per arm. Injected fresh per call from `/state`.

TASK_INSTRUCTIONS: the user's task plus the output format (JSON array of `{arm_id, angle}` steps).

### 7.5 Validation firewall

Every LLM output goes through `plan_validator.py`:
1. Parse JSON (reject if invalid)
2. Check arm_id values exist in config
3. Check angles are 0-180
4. Simulate step by step: compute tip positions, verify no collision
5. If any check fails: reject, do not execute

The LLM is never trusted. It is a suggestion engine. Python is the authority.

### 7.6 New modules

- `app/services/llm_service.py` — wraps Claude or GPT-4 API call
- `app/services/prompt_builder.py` — builds system prompt from live state
- `app/services/plan_validator.py` — checks plans against physics
- `app/services/plan_executor.py` — executes plans via `/set_angle` with timing

### 7.7 New endpoint

`POST /plan` — takes `{"task": "Move object from intake to output"}`, calls LLM, validates, returns the plan (or error).

---

## 8. Future: Phase 3 — multi-agent coordination

### 8.1 What it adds

Each arm gets its own LLM agent. A central brain (LangGraph) assigns high-level tasks. Arm agents coordinate peer-to-peer via a message bus, only escalating to the central brain on deadlock.

### 8.2 Architecture

```
Central Brain (LangGraph)
    |
    +── Receives task, decomposes into subtasks
    +── Monitors agent states
    +── Intervenes on deadlock
    |
    ├── Arm 1 Agent (LangChain) ──┐
    ├── Arm 2 Agent (LangChain) ──┤── MessageBus
    └── Arm N Agent (LangChain) ──┘   (SocketIO now, MQTT later)
```

Message types between agents:
- `handoff_ready` — "I'm at the zone, holding the object"
- `zone_clear` — "I've exited, you can enter"
- `subtask_complete` — "My part is done"
- `help_needed` — "I'm stuck, central brain should intervene"

### 8.3 Why LangGraph

Robotic arm coordination is a state machine: idle → moving → at_handoff → waiting → handing_off → idle. LangGraph's graph-based state transitions map directly to this. Each node is an arm agent; edges are coordination events. CrewAI is too workflow-oriented. AutoGen is too chat-centric.

### 8.4 Communication: SocketIO now, MQTT later

SocketIO works for browser-to-server and 3-10 agents on a single server. Real robotics uses MQTT — lightweight pub/sub with 2-byte overhead, designed for constrained devices.

The plan: define a `MessageBus` abstract interface. Phase 2 uses SocketIO. Phase 3 swaps to MQTT for arm-to-arm while keeping SocketIO for the browser dashboard.

```python
class MessageBus:
    def publish(self, topic: str, message: dict) -> None: ...
    def subscribe(self, topic: str, callback) -> None: ...

class SocketIOBus(MessageBus): ...  # Phase 2
class MQTTBus(MessageBus): ...      # Phase 3
```

---

## 9. What changes for real hardware

The API layer stays the same. Whether `/set_angle` drives a canvas drawing or a physical servo, the HTTP interface is identical.

| Component | Simulation | Real hardware |
|-----------|-----------|---------------|
| /set_angle handler | Updates in-memory dict | Sends command to motor controller |
| State feedback | Instant | Encoder reading, 10-50ms delay |
| Collision detection | Rule-based zone checks | Proximity sensors + kinematics |
| Arm-to-arm comms | SocketIO | MQTT |

When hardware arrives, a thin adapter sits behind `/set_angle`. In simulation: update dict, emit SocketIO. In hardware: send motor command, read encoder, update state, emit SocketIO. Everything above the adapter — scripts, LLM planner, agent coordination — stays unchanged.

---

## 10. Scaling

| Scale | Arms | Communication | Orchestration |
|-------|------|--------------|---------------|
| Lab demo | 3-10 | SocketIO | Docker Compose |
| Facility | 10-50 | MQTT broker | Docker Compose or Kubernetes |
| Warehouse | 50+ | MQTT + Redis | Kubernetes |

The `pipeline.yaml` config and `generate_compose.py` pattern handles the first two. Kubernetes migration means replacing the compose generator with a Helm chart generator — same YAML-in/YAML-out approach.

---

*End of Architecture Document*
