# INTER.act - Project Goal

## Project Goal

Build a multi-agent simulation system in which autonomous actors - robotic arms and Automated Guided Vehicles (AGVs) - self-organize to complete a transport task from a given input to a given output, **without any central controller deciding how**.

The system receives a plain-English goal (e.g., _"Move 3 objects from Input to Output, shortest time"_), interprets it, and then lets the actors negotiate among themselves - via a shared message bus - to agree on a path, assign roles, and execute. The path is not pre-defined. The actors define it at runtime.

## Core Problem

Traditional warehouse and factory automation relies on a **central orchestrator** that knows all routes, assigns all tasks, and tells every actor what to do step by step. This works at small scale. It breaks in the real world because:

1. **The complexity of real-world situations cannot be pre-programmed.** A warehouse with 50 workers, forklift drivers, and 100 AGVs generates a number of possible state combinations that no developer can enumerate ahead of time. When an unpredicted event happens - a forklift blocking an AGV lane, an arm failing mid-transfer - the central controller has no answer. The system deadlocks.

2. **A deadlock in a deterministic central system is unrecoverable by the system itself.** It requires a human to intervene, re-route, and restart. This is not acceptable at scale.

3. **Non-deterministic constellations of actors cannot be handled by pre-written scripts.** The real world does not follow a script. The number of permutations of states and actor positions is too large to implement monistically ahead of time. This is the actual, fundamental limitation - not latency or raw computational power, as those are symptoms, not the cause.

The solution is to move intelligence from a central unit into each actor, and let the actors negotiate the solution themselves.

---

## Strategic Vision

The long-term intent is a system where:

- Any number of actors can join a shared environment.
- A human or system gives only a **goal**, not a procedure.
- Actors independently determine who does what, in what order, via what path.
- No central authority assigns roles. No developer writes route logic.
- The system finds **non-deterministic solutions** - solutions that were never explicitly programmed, but emerge from actor negotiation and constraint satisfaction.

This is the same coordination model used by ant colonies, autonomous drone swarms, and modern warehouse robot fleets (e.g., Amazon Robotics). The research contribution is demonstrating this model in a configurable, observable, locally-run simulation environment.

---

## Functional Objectives

1. Accept a plain-English task prompt as input (e.g., _"Move 5 objects from I to O, shortest distance"_).
2. Interpret the prompt into a structured task: source, destination, constraint, object count.
3. Broadcast the task to all actors on a shared message bus.
4. Allow each actor to declare its own capabilities: which zones it can reach, its position (2D coordinate), its speed, and its capacity.
5. Let actors negotiate via the message bus to agree on a path and assign which actor does which leg.
6. Execute the agreed path: actors communicate directly only with their immediate neighbor once the plan is set.
7. When an actor fails mid-task, it notifies its neighbor; the affected part of the chain halts without crashing unrelated actors.
8. When a path or actor becomes unavailable, broadcast that information to the bus so others can re-plan or report that the task cannot be completed right now.
9. Log all messages exchanged between actors to a visible output (the message bus log panel) for transparency and debugging.

---

## System Understanding

### The Key Conceptual Shift

Earlier INTER.act phases used a centralized demo-runner that knew the pipeline topology, controlled execution order, and explicitly triggered each transfer step. That architecture is now deprecated.

In the current target architecture:

- No central execution orchestrator decides which actor moves when.
- The previous handoff-style execution flow is removed in favor of decentralized negotiation.
- The "Run All" interaction is replaced by a **goal-driven text input** where the user describes the desired outcome.
- Each actor owns its own local decision loop and reacts only to its own state, capability profile, and received messages.
- Actors discover available participants dynamically through the shared message bus and negotiate execution paths at runtime.
- After negotiation, execution becomes peer-to-peer between neighboring actors instead of centrally coordinated.

### What an Actor Knows About Itself

Every actor has a predefined **capability profile** (stored in a behavior file, not computed at runtime):

- Which named zones it can physically reach (e.g., Input, Output, A1 dock, A2 dock)
- Its own 2D canvas coordinates
- Its speed (for AGVs: in meters per second or equivalent canvas units)
- Its capacity (how many objects it can carry at once)
- Its current status (free / busy / failed)

These are the only facts an actor knows about itself. It does not know what other actors can do until they broadcast that information.

### What an Actor Does Not Know at the Start

- Which other actors exist
- Who will be its upstream or downstream neighbor
- What path will be taken
- What the task is - until the interpreter broadcasts it

This is intentional. The path emerges from negotiation, not from a pre-written topology.

---

## Architecture / Workflow

```
┌──────────────────────────────────────────────┐
│  User Input (text prompt)                    │
│  e.g., "Move 3 objects from I to O,          │
│          shortest time"                      │
└──────────────┬───────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────┐
│  Interpreter + Negotiation Coordinator       │
│  QwQ-32B (Q4_K_M, temp=0)                     │
│  Parses goal + proposes path plan            │
└──────────────┬───────────────────────────────┘
               │  Structured task + path plan
               ▼
┌──────────────────────────────────────────────┐
│             Message Bus                      │
│  (Central shared channel - all actors        │
│   subscribe and publish here)                │
└──┬───────┬────────────┬──────────┬───────────┘
   │       │            │          │
   ▼       ▼            ▼          ▼
 Arm 1   Arm 2        AGV 1      AGV 2  ...
 (A1)    (A2)         (V1)       (V2)
   │       │            │          │
   └──────────────┬─────────────────────────────
                  ▼
┌──────────────────────────────────────────────┐
│  Actor brains: Qwen2.5-14B (Q4_K_M, temp=0)  │
│  State + mailbox → script choice             │
└──────────────────────────────────────────────┘
```

**Phase 1 - Capability Broadcast (on message bus):**

Each actor publishes its profile to the bus.
AGVs dock directly at the source and destination arms (no handoff zones).

```
A1: can_reach=[I],        time=10s
A2: can_reach=[O],        time=10s
V1: can_reach=[A1, A2],   position=[5,3],  speed=2m/s,  capacity=1
V2: can_reach=[A1, A2],   position=[5,4],  speed=4m/s,  capacity=2
V3: can_reach=[A1, A2],   position=[5,5],  speed=10m/s, capacity=3
```

**Phase 2 - Negotiation and Path Agreement (on message bus):**

The coordinator proposes a path plan based on capability broadcasts; actors
accept the plan and enter their assigned roles.

Given the task "move from I to O" with constraint "shortest time":

- A1 is required (only one that can reach Input).
- A2 is required (only one that can reach Output).
- Among V1, V2, V3 - all can reach both arms - the constraint "shortest time" means V3 is preferred (fastest).
- Actors agree on path: `A1 → V3 → A2`
- The chosen actors enter "busy" state. Non-chosen actors remain free for other tasks.

**Phase 3 - Execution (peer-to-peer, off the bus):**

Once the path is agreed:

- A1 picks the object and signals V3 directly: "I'm done, pick up here."
- V3 transports to A2 and signals A2 directly: "I'm done, pick up here."
- A2 places at output and broadcasts to the bus: "Task complete."

Peer-to-peer messages after planning do **not** go through the message bus - only the initial negotiation and status broadcasts do. This keeps bus traffic manageable.

**Phase 4 - Failure Handling:**

- If an actor fails mid-task, it broadcasts "path blocked" / "I am unavailable" to the bus.
- Downstream actors in the current chain halt.
- Actors not in the chain are unaffected.
- The bus can trigger a re-planning attempt with the remaining actors, or report "task cannot be completed right now."

---

## Key Components

| Component                      | Role                                                                                                                                                                                               | Current Status                                                       |
| ------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| **Text Input / Prompt UI**     | Replaces "Run All" button. User types the goal in plain English.                                                                                                                                   | Not yet built                                                        |
| **Interpreter**                | Parses the prompt into a structured task (source, destination, constraint, count). Likely needs a model larger than 0.5B.                                                                          | Not yet built                                                        |
| **Message Bus**                | Shared channel all actors subscribe to. Used for capability broadcast, negotiation, path agreement, and status updates.                                                                            | Partially designed; not implemented                                  |
| **Arm Actor (with LLM brain)** | Robotic arm. Has a local Ollama LLM (0.5B). Reads its own input/output status and neighbor messages. Decides: wait, pick, or hold.                                                                 | Implemented (5 arms, peer mailboxes demonstrated by one team member) |
| **AGV Actor**                  | Automated Guided Vehicle. Replaces the vehicle/shuttle from Phase 1. Knows position, speed, capacity. Brain status TBD.                                                                            | Scripted currently; brain TBD                                        |
| **Behavior File**              | Per-actor static configuration: reachable zones, speed, capacity. Defines the actor's capability space. The LLM chooses _scripts_; the behavior file defines _what scripts exist_.                 | Partially exists (3 scripts for arms: wait / pick / hold)            |
| **Peer Mailbox**               | Per-actor inbox for direct neighbor messages during execution. One team member already demonstrated this.                                                                                          | Demonstrated in prototype                                            |
| **Canvas / Visual**            | 2D rendering of actors, objects, and message activity. Message bus log visible as output panel.                                                                                                    | Implemented; needs update for new topology                           |
| **Observer**                   | Sets the goal, monitors system state, visualizes negotiation and execution, and intervenes only during unrecoverable deadlock conditions. Does not orchestrate execution or assign actor behavior. | Passive monitoring role only                                         |

---

## Constraints and Requirements

### Hard Constraints (non-negotiable, stated explicitly in meetings)

- **No central orchestrator for execution.** The demo-runner must not be the one deciding which arm moves when. It can start the process, but not control it step by step.
- **0.5B model (Ollama `qwen2.5:0.5b`) is the current LLM for actor brains.** It cannot reliably do arithmetic. Distance and time calculations must be pre-computed or provided to the actor, not generated by the LLM. The LLM chooses scripts; it does not calculate routes.
- **Conveyors are removed.** Only `arm` and `vehicle` (renamed AGV) actor types exist. Any `type: conveyor` in config raises an error.
- **Capability profiles are predefined.** An actor knows its own reach, speed, and position from its configuration - it does not discover these dynamically.
- **Prompt constraints drive path selection.** The system must support at minimum three modes: unconstrained (`I → O`), shortest time (`I → O, shortest time`), and shortest distance (`I → O, shortest distance`).

### Operational Constraints

- **MVP first.** The priority is to demonstrate actor communication and basic constraint-based decision making. Package weight, variable capacity, and multi-stop routing are explicitly deferred.
- **Same-speed vehicles as starting point.** For the first implementation, if all vehicles have equal speed, selection by position (nearest vehicle) is the default decision criterion. Differentiated speeds and capacities are layer two.
- **Path blocking must be communicable.** When an actor is busy or has failed, it must be possible to broadcast this to the bus so that other actors don't attempt to use it.
- **Local execution.** The entire system runs on a single machine via Docker Compose. No cloud dependencies. LLM runs via local Ollama instance.
- **Local execution.** The default deployment runs on a single machine via Docker Compose. LLMs run via a local Ollama instance or optionally via cloud-hosted/remote LLMs where available.

### Performance Constraint (observed)

- The 0.5B model takes approximately 3–5 seconds per decision cycle per arm under current conditions. This is a known bottleneck. Multi-arm parallelism makes the simulation slow but functional. This is acceptable for a research demonstration.

---

## Model Selection

> **Note:** This section supersedes the 0.5B note in _Hard Constraints_ above. It is the result of a separate analysis of the three cognitive roles the system actually has, and the model properties each one needs.

The 0.5B actor brain (Ollama `qwen2.5:0.5b`) is a known limitation. It cannot do arithmetic reliably, its JSON output is inconsistent, and once there are three or more vehicles at different positions and speeds with a constraint like "shortest time," it picks the wrong vehicle often enough that the consensus breaks. Scaling up across the board is one option, but the three cognitive roles in this system have genuinely different requirements, so a single model size is the wrong tradeoff for at least one of them.

The chosen architecture is a **two-model heterogeneous deployment**:

| Model           | Quantization | Assigned To                           | Temperature |
| --------------- | ------------ | ------------------------------------- | ----------- |
| **Qwen2.5-14B** | Q4_K_M       | All actor brains (arms + AGVs)        | 0.0         |
| **QwQ-32B**     | Q4_K_M       | Interpreter + Negotiation coordinator | 0.0         |

Both models come from the same Qwen family. They share tokenization patterns, instruction-following conventions, and JSON formatting behavior. A prompt that works for Qwen2.5-14B transfers almost directly to QwQ-32B, with the only extra step being the `<think>...</think>` block that has to be stripped from QwQ's output before downstream parsing.

### Why this split

The actor brain runs on the hot path. Eight actors (5 arms + 3 AGVs) hit the same Ollama instance every decision cycle, and any reasoning-style model with a thinking phase makes the simulation feel frozen. Qwen2.5-14B is a non-reasoning instruction-tuned model: fast enough per call to keep the round-trip survivable, large enough that simple state-to-script matching is stable at temperature 0.

The Interpreter and the negotiation coordinator run once per task, not per cycle. They are allowed to take 15–45 seconds, because the user is waiting for a plan, not for a frame to render. QwQ-32B's explicit chain-of-thought is actually useful here: when it picks the fastest vehicle, the reasoning shows up in the message bus log and is debuggable. A non-reasoning 32B model would land the same answer most of the time but fail silently on edge cases, which is the worst kind of bug in a research demo.

### Why not the obvious alternatives

- **A single 7B model for everything.** The 0.5B failure is the smaller cousin of what 7B exhibits under negotiation pressure. The speed win does not justify reintroducing the same class of bug.
- **A reasoning model (QwQ, DeepSeek-R1) on actor brains.** Thinking tokens add 3–8 seconds per call. With 8 actors in a queue, a full decision round would take several minutes. The simulation would look broken.
- **A single homogeneous model across all roles.** The actor brain and the negotiation coordinator have incompatible optimal properties: low latency vs. high reasoning quality. Forcing one model means making the wrong tradeoff for one of them.
- **DeepSeek-R1-Distill-32B for the Interpreter.** Its reasoning is strong, but Qwen's instruction tuning carries through into QwQ more cleanly, so the final answer after the thinking block is more reliably a bare JSON object. That matters when the output is machine-parsed.

### Deployment chart

```
┌─────────────────────────────────────────────────────────────┐
│              User prompt:                                   │
│              "Move 3 objects from I to O, shortest time"    │
└──────────────────────────┬──────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│   QwQ-32B  (Q4_K_M, temp=0)   - runs once per task         │
│                                                             │
│   Role 1: Interpreter                                       │
│     prompt → {source, destination, constraint, count}       │
│                                                             │
│   Role 2: Negotiation coordinator                           │
│     all capability declarations + constraint → path plan    │
│                                                             │
│   Latency budget: 15–45 s  (acceptable, one-off)            │
└──────────────────────────┬──────────────────────────────────┘
                  │  Structured task + path plan
                  ▼
┌─────────────────────────────────────────────────────────────┐
│                       Message Bus                           │
└──┬──────────┬──────────┬──────────┬──────────┬──────────────┘
  │          │          │          │          │
  ▼          ▼          ▼          ▼          ▼
┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐
│ Arm 1  │ │ Arm 2  │ │ AGV 1  │ │ AGV 2  │ │ AGV 3  │  ...
└────┬───┘ └────┬───┘ └────┬───┘ └────┬───┘ └────┬───┘
    │          │          │          │          │
    └──────────┴──────────┴──────────┴──────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│   Qwen2.5-14B  (Q4_K_M, temp=0)   - runs every cycle       │
│                                                             │
│   Role 3: Actor brain                                       │
│     state + mailbox → script choice (wait / pick / hold)    │
│                                                             │
│   Latency budget: 1–3 s per call  (hot path)                │
└─────────────────────────────────────────────────────────────┘
```

### What this commits us to

Three costs are acknowledged upfront:

1. A full decision round across 8 actors takes 12–20 seconds. The simulation is not real-time. This is the honest price of using a model whose script selection is stable.
2. The negotiation is not fully distributed in this MVP. A single QwQ call evaluates every actor's capability declaration and outputs the path plan; the actors then execute their assigned roles. The research claim - that path choice emerges from declared capabilities under a constraint - is preserved, but the consensus itself is centralized in one model call rather than spread across N actors. Fully distributed consensus is left for a later phase.
3. Two Ollama models, two prompt templates, and a thinking-block stripper for QwQ output. Small but real maintenance cost compared to a single-model setup.

**Confidence: ~82%.** The main sources of uncertainty are: (a) Qwen3 availability - if Qwen3-30B is reachable via Ollama at deployment time, its per-call thinking toggle could collapse this back into a single model; (b) the unresolved question of whether the negotiation must eventually be fully distributed, which would change the QwQ-as-coordinator framing; (c) actual GPU VRAM. QwQ-32B at Q4_K_M needs roughly 20 GB, and without that the Interpreter call falls back to CPU offloading at minutes-per-call latency.

---

## Scope Boundaries

### In Scope

- Arm actors with local LLM brains choosing from predefined scripts
- AGV actors with position, speed, and reach capabilities
- Plain-English prompt input → structured task via Interpreter
- Message bus for actor negotiation and capability broadcasting
- Peer-to-peer direct communication between actors after path is agreed
- Constraint-based vehicle selection (nearest / fastest)
- Failure detection and neighbor notification
- Visual 2D canvas representation of the pipeline
- Message bus log visible in UI
- Multiple AGVs in parking zone, selected at runtime
- Parallel task handling (free actors picked up for new tasks while others are busy)

- Node-RED style drag-and-drop builder - implemented (done)
- Cloud-hosted or remote LLMs - supported (implemented)

### Out of Scope (explicitly deferred or rejected)

- **Centralized execution orchestration** - explicitly removed from the target architecture
- **Legacy handoff-style execution pipelines** - deprecated in favor of decentralized negotiation
- **Package weight and size constraints** - future work, not MVP
- **Collision avoidance between AGVs on a 2D grid** - not in current scope; AGVs do not share a floor grid with path-blocking logic per vehicle
- **Multi-input, multi-output warehouse topologies** - considered for later phases
- **Real physical hardware integration** - this is a simulation only

---

## Final Interpreted Understanding

INTER.act is building a research demonstration of **decentralized multi-agent coordination** applied to a warehouse-like simulation. The system's central claim is: _give actors a goal, not a procedure, and let them find the solution themselves._

The immediate engineering target is:

1. Replace the prompt-less "Run All" button with a text input that feeds a goal to an Interpreter.
2. The Interpreter structures the task and publishes it to a message bus.
3. All actors - arms and AGVs - subscribe to the bus, declare their capabilities, and negotiate which of them handles which leg of the task.
4. The negotiated path is executed peer-to-peer, not through a central runner.
5. The UI makes the negotiation and execution visible: message bus log, actor states, object movement on canvas.

The system does not need to be perfect. It needs to be **demonstrably non-deterministic** in how it finds solutions - meaning the path taken should emerge from actor negotiation based on the constraint given in the prompt, not from code that pre-defines the route. When the constraint is "shortest time," the fastest available AGV wins. When the constraint is "shortest distance," the nearest one does. When no constraint is given, the first actor to respond wins. None of these decisions are hard-coded. They fall out of the negotiation.

That is the project. Everything else (conveyor belts, complex routing, package weights, collision avoidance, the visual orchestration workspace) is downstream of making this core loop work first.
