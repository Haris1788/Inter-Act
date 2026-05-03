# Integration — AI on multi-asset pipeline

Compact record of the integration that brings the Qwen brain layer
together with the multi-asset pipeline (5 arms + 1 shuttle + 1 conveyor).
Caveman style on purpose — short sentences, plain words, no jargon
unless it earns its keep.

---

## What was before

Two roads. They did not meet.

| Road | What it had | What it did not have |
|---|---|---|
| `dev-haris` (AI brain) | Qwen brain. 3 arms. 3 scripts per arm. Closed menu. Safety-net rule. | Only 3 arms, hardcoded geometry. No shuttle, no conveyor, no asset abstraction, no parallelism. |
| `dev-sneh` (pipeline) | 5 arms + 1 shuttle + 1 conveyor. Common `Module` base class. Backpressure via `TransferPoint`. Auto-pipeline driver. | No AI. No brain. No script menu. |

Demo on the AI road: spawn one object, click `Run All`, 3 arms walk it
through, each arm asks Qwen which of 3 scripts to run. One object at a
time. Sequential.

Demo on the pipeline road: click `Run Demo`, 5 arms + shuttle + conveyor
walk 3 objects through autonomously, no LLM in the loop.

---

## What has been updated

This integration branch (`feature/Integration-AI-on-multi-assets`) is
created from `dev-haris` with `dev-sneh` merged in. Each piece of work
since the merge:

### Plumbing (one-time after merge)

- Regenerated `docker-compose.yml` from `pipeline.yaml` (`python scripts/generate_compose.py`) so all 5 arms + shuttle + conveyor are defined.
- `scripts/demo_runner_server.py` no longer forwards a stale `use_brain` arg; the auto-pipeline runs cleanly.

### Brain — generalised across all arms

[app/services/arm_brain.py](app/services/arm_brain.py)

- Hardcoded `per_arm_facts = {1: …, 2: …, 3: …}` is gone. Each arm reads its own upstream / downstream zone names from `pipeline.yaml` at call time, so adding more arms in YAML is config-only.
- Safety-net rule extended: forces `hold_*` not just when source is empty, but also when any **intermediate asset** between this arm and the previous arm is failed / draining / stopped — and when the **immediate downstream actor** is dead.
- New **fast-path**: when the answer is forced ("source empty" or "intermediate dead"), the brain returns `hold_*` immediately *without* calling Qwen. Idle polls cost ~1 s instead of 5–30 s.

### Scripts — generic, derived from flow order

[app/services/arm_scripts.py](app/services/arm_scripts.py)

- 9 hand-written arm functions → 3 generic functions (`_relay_arm`, `_hold_arm`, `_return_to_rest`) parameterised by `arm_id`.
- New asset script bodies: `_relay_vehicle` (animates shuttle waypoints), `_relay_conveyor` (animates slot shifts), and matching holds.
- Registry built at module load from `pipeline_config.get_arm_ids()` + `pipeline_config.get_extra_assets()`. Adding an arm or asset to YAML grows the menu automatically.
- Hold scripts accept `quiet=True`. In quiet mode they skip the rotation animation and emit nothing — true no-op for repeat polls.

### Endpoint — actor-aware

[app/routes/arm_routes.py](app/routes/arm_routes.py)

- `POST /run_script` now accepts `actor_id` (string, e.g. `"veh-a"`) in addition to legacy `arm_id` (int).
- Assets use a deterministic mini-brain (`_pick_for_asset`) — closed menu + state lookup, no LLM.
- New `GET /topology` returns `["arm-1","arm-2","veh-a","conv-1","arm-3","arm-4","arm-5"]` so the UI can walk the flow.
- New `GET /actor_scripts/<id>` returns the menu for any actor.
- `POST /objects/spawn` now emits the new `owner_type` / `owner_id` namespace so the canvas can position the spawned object at intake.
- **Per-actor "last decision" memory** suppresses log spam: a repeat `hold_*` for the same actor with no world change emits nothing — no `script_chosen`, no `notify_processing`, no `task_complete`.

### Frontend — parallel loops + AI bar

[app/static/js/ui/controls.js](app/static/js/ui/controls.js), [app/static/js/core/socket.js](app/static/js/core/socket.js), [app/templates/index.html](app/templates/index.html), [app/static/css/style.css](app/static/css/style.css)

- New AI bar with: **Reset**, **Spawn obj**, **Run All (Qwen)**, **Run Arm 1…5**, **Run Shuttle A**, **Run Belt 1**.
- Per-actor buttons auto-render from `state.assets` in flow order so they adapt to topology.
- **Run All starts N parallel loops**, one per actor. Each loop polls its actor independently; arm-1 picks Obj2 from intake while arm-2 carries Obj1 forward — no waiting.
- Each loop has idle backoff: poll fast (200 ms) right after a relay, slow (up to 5 s) after consecutive idle holds.
- Run All button doubles as Stop while looping.
- Reset and Spawn stay clickable mid-run.
- New `script_chosen` socket handler renders Qwen's pick + safety-net override into the existing log panel. Honest labels: `🛌 idle` for rule fast-path, `🎬 Qwen chose` for actual LLM consultation, `⚖️ Override →` for safety-net corrections.

---

## How to build and run a set up

### First time / after pulling changes

```bash
# Project root.

# 1. If pipeline.yaml changed, regenerate the compose file:
python scripts/generate_compose.py

# 2. Build images and bring everything up:
docker compose up -d --build

# 3. Wait for health checks (≈30 s on a cold boot):
docker compose ps        # should show all containers running, key ones (healthy)
```

### Daily / between code changes

```bash
docker compose up -d --build backend frontend       # rebuild only what changed
docker compose restart demo-runner                  # if scripts/run_demo.py changed
```

### Stop / clean

```bash
docker compose down                                 # stop everything, keep volumes
docker compose down -v                              # also drop volumes (Ollama model cache)
```

### Endpoints

- UI: <http://localhost:8080>
- Backend API: <http://localhost:5000>
- Demo runner (auto-pipeline trigger): <http://localhost:6000>
- Ollama (Qwen): <http://localhost:11434>

### Watch the AI in a terminal

```bash
docker compose logs -f backend | grep -E "🎬|🛌|🤖|⚖|▶|■"
```

---

## What Qwen can do now

### Per-arm script selection (the "AI moment")

Each arm has its own `ArmBrain` (singleton, [app/services/arm_brain.py](app/services/arm_brain.py)). When an arm has actual work to do, the brain hands Qwen:

```
You are arm 3. You can reach handover_2 (180°) and handover_3 (0°).
MENU:
  - relay_handover_2_to_handover_3: pick at handover 2, drop at handover 3
  - hold_at_handover_2:             face handover 2 and wait
  - return_to_rest:                 go to 90°
RULE: pick relay_* only if there is a real object at YOUR source.
AVAILABILITY: there IS an object at handover_2.
Output ONLY JSON: {"script": "<name>", "reasoning": "<one sentence>"}
```

Qwen replies with one script name. The backend then:

1. **Fuzzy-matches** the name against the menu (catches typos).
2. Applies a **deterministic safety net** — if Qwen contradicts the world (says relay when source is empty, or hold when work is available, or relay when intermediate assets are dead), the rule wins and **both** the AI suggestion and the override are logged.
3. **Runs the chosen script.** The script rotates the arm + emits `object_update` events; the canvas animates.

### Where Qwen runs vs where rules run

```
                                ┌─────────────────────────────────┐
                                │  /run_script for arm-N          │
                                │  (called repeatedly by the      │
                                │   parallel actor loops)         │
                                └──────────────┬──────────────────┘
                                               │
                ┌──────────────────────────────▼──────────────────────────────┐
                │  ArmBrain.pick_script                                       │
                │                                                             │
                │  fast-path: source empty?    → hold (no Qwen)               │
                │  fast-path: assets dead?     → hold (no Qwen)               │
                │  otherwise:                  → ASK QWEN  (3–10 s on CPU)    │
                │  fuzzy-match + safety net    → final script                 │
                └──────────────────────────────┬──────────────────────────────┘
                                               │
                                               ▼
                ┌─────────────────────────────────────────────────────────────┐
                │  arm_scripts.run_script                                     │
                │  rotate arm + emit object_update + log_message events      │
                └─────────────────────────────────────────────────────────────┘
```

### What the assets do

The shuttle (`veh-a`) and the conveyor (`conv-1`) have **smaller menus** (2 options: relay / hold). Their decisions are made by a deterministic mini-brain — closed menu + state lookup, no LLM. Same closed-menu pattern, no Qwen call. Their scripts physically animate the shuttle across waypoints and the conveyor through slots.

### Coordination between actors

**No actor messages another actor.** Each one reads its own slice of the world (object positions, asset health, transfer-point occupancy) and decides. The shared world state is the coordination protocol.

If arm-2 drops an object onto the shuttle, arm-2's view says "downstream now occupied". On the next poll, the shuttle's view says "input now occupied" → it relays. No phone call, no message; just both actors looking at the same world.

### Failure handling

Kill the shuttle or the conveyor while the pipeline is running:

- The actor whose immediate downstream is dead → forced `hold_*` (logged as `⚖️ Override → hold_at_… (intermediate asset(s) not alive)`).
- Arms downstream of the dead asset → forced `hold_*` (the upstream chain is broken; objects can't legitimately have crossed).
- The dead asset itself → forced `hold_*`.
- Recover the asset → next poll the actor sees it alive and resumes.

### Observable behaviours

- **Spawn many objects, click Run All once.** All objects walk to output; arms work in parallel (arm-1 picks Obj2 from intake while arm-2 carries Obj1 forward).
- **Spawn while running.** New objects join the line within ~1–5 s; nothing else needed.
- **Kill / recover any asset mid-run.** Pipeline naturally pauses + resumes around the failure window.
- **Idle pipeline.** Each actor logs ONE `🛌 idle: hold_at_<source>` line then goes silent. Polling continues quietly under the hood; new spawns wake everyone within 1–5 s.
- **Operator visibility.** Every Qwen consultation appears in the log as `🎬 Qwen chose: <script> — <reasoning>`; every safety-net override appears as `⚖️ Override → <script> (<reason>)`. No black-box decisions.

---

## Known gaps (deliberate, tracked)

- **Qwen 0.5 B is dumb.** It writes nonsense reasonings often; the safety net catches actual contradictions. Bigger model (`qwen2.5:7b`, ~4.5 GB, one env var change) eliminates this without changing the architecture.
- **Conveyor tick rate vs Qwen latency.** The conveyor's continuous slot-shift loop runs every 50 ms; we cannot ask Qwen at that rate. The asset's mini-brain is deterministic and fast — by design. Adding LLM-driven shift decisions would need a per-object cache layer, future work.
- **Manual demo path is decoupled from the auto-pipeline.** `Run Demo` (auto-pipeline, no AI) and `Run All (Qwen)` (AI manual demo) are two separate flows. They share the canvas but should not run together; the AI demo writes object state directly while the auto-pipeline drives object state through `Module` threads. If both run together, the world state will get scrambled.

---

## Next user story (size + weight constraints)

Now that the integration is solid, the next user story adds object size and weight, plus per-actor capacity limits. The closed-menu + safety-net pattern is the right shape — it just needs:

- One more script per actor: `reject_oversized`.
- One more rule clause in the safety net: "if upstream object exceeds my capacity → reject".
- One frontend dialog change: spawn picks size + weight.

Estimated effort: about a day, no new files needed beyond a maybe-new `app/models/object_attributes.py`.
