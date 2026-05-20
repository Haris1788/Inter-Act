# (20 May 2026) AI Integration

> The day the two-layer AI actually drove the warehouse.
> TUC GPU models wired in; master planner → per-agent tactical coordination. ~10-minute read.

---

## What was before today

The AI layer was **built but disconnected from the robots**:

- `llm_service.py`, `interpreter`, `central_brain`, `ai_routes` already existed in the
  codebase, but the backend talked to a **local** Ollama container that only held a
  397 MB `qwen2.5:0.5b`. The heavy TU Clausthal GPU models (`qwq:32b`, `qwen2.5:14b`)
  were never reached.
- `POST /plan` produced a plan, published it on the SocketIO bus for the browser to
  see, and **nothing executed it**. The master plan died in memory.
- AGV / arm brains followed a fixed deterministic step list. The tactical model was a
  fallback consulted **only when a step failed**.

In one line: **before today**, the AI could plan, the plan never reached the robots,
and the robots had no way to reason about each other. **Today, all three are fixed —
see the next section.**

---

## What I did today

Three deliverables that together turn the disconnected AI layer into a working
master-and-workers system:

1. **Wired the backend to the real TU Clausthal GPU models.** `qwq:32b` for
   interpretation + planning, `qwen2.5:14b` for per-agent tactical decisions — reached
   over the existing persistent reverse-SSH tunnel. The legacy local `qwen_brain`
   (per-arm 0.5b) is **kept intact** — the two clients are deliberately on different
   env vars so the old path is undisturbed.

2. **Closed the plan → agents gap.** NEW file `app/services/plan_executor.py`.
   `POST /plan` now (a) produces the plan with `qwq:32b` AND (b) delivers each
   assignment to the right AGV container via `POST http://agv-N:8100/assign`.
   **The master AI plan actually drives the robots** — before today this was the
   broken link.

3. **Made the agents AI-coordinated.** AGV brain rewritten so `qwen2.5:14b` is
   consulted **before every script** with the live shared status. Arms confirm
   load/unload via the same model. The 12 hardcoded scripts are unchanged — the AI
   decides only **when** each script runs and whether to hold to avoid colliding with
   another AGV.

---

## Before vs After

|                       | Before today                                              | After today                                                                 |
| --------------------- | --------------------------------------------------------- | --------------------------------------------------------------------------- |
| **Models**            | local `qwen2.5:0.5b` only                                 | TUC `qwq:32b-q4_K_M` (plan) + `qwen2.5:14b-instruct-q4_K_M` (tactical)       |
| **Endpoint**          | `http://ollama:11434`                                     | `http://host.docker.internal:11500` (reverse-SSH tunnel to cloud-247)       |
| **Plan → robots**     | plan published to browser, never executed                 | `plan_executor` POSTs every assignment to the AGV containers                |
| **AGV decisions**     | fixed `_NORMAL_STEPS`; AI only on step failure            | qwen14b decides **every** step (proceed / hold / fault) from shared status before execution   |
| **Coordination**      | lock blocking only                                        | each AGV's AI reads `/coord/snapshot` and holds for others (AI collision-avoid) |
| **Arms**              | blind timed animation                                     | qwen14b confirms "load/unload now?" before each sweep                        |
| **qwen_brain (0.5b)** | shared `OLLAMA_BASE_URL`                                   | decoupled — stays on local container via its own var (untouched)            |

---

## How the flow happens — prompt → AGVs moving

A walkthrough you can read aloud during the demo, file references included so anyone
can open the code afterwards.

### Step 1 — you type a prompt

The browser sends `POST /plan {"prompt": "Sort 3 packages by rainbow color, one per AGV"}`.

### Step 2 — the Interpreter understands the intent  (qwq:32b on TUC)

- File: `app/agents/interpreter.py`
- Output: a structured **TaskSpec** —
  `{intent: sort, package_count: 3, ordering: [red, orange, yellow], agv_ids: [agv-1, agv-2, agv-3]}`.

### Step 3 — the Master Planner assigns the work  (qwq:32b, same warm load)

- File: `app/agents/central_brain.py`
- Output: a **Plan** —
  `{assignments: [{package_id, agv_id, sequence, route, label}, ...], arm_roles: {input_arm: …, output_arm: …}}`.
- Interpreter + Planner share **one** GPU load (`planning_window` context) — no model swap between them.

### Step 4 — plan_executor delivers the plan to the AGVs and arms

- File: `app/services/plan_executor.py` (new today)
- For each assignment, in sequence: `POST http://agv-N:8100/assign` with the assignment dict.
  AGV containers spawn their brain in a daemon thread.
- The plan is also published on the **SocketIO message bus** — the browser sees it
  (`plan` event), and `_emit_log` lines stream into the log panel.
- The arm load/unload choreography starts as background threads in `dispatch_routes.py`,
  AI-gated (see Step 5).
- The planning window now closes — the GPU is free to swap to `qwen2.5:14b` for execution.

### Step 5 — each AGV / arm reads the shared status and asks the tactical AI

This is the *coordination* layer.

- **Shared status** lives at `GET /coord/snapshot` (in `app/routes/agv_routes.py`): every
  AGV's cell / status / cargo, lane holder, station holders, arm health, the live
  assignments. Every brain reads this **before each step**.
- Each AGV brain (`scripts/agv_brain.py`) asks `qwen2.5:14b` via `POST /ai/tactical`:
  *"given my position, my planned next step, and what the others are doing — proceed,
  hold, or fault?"*
- **HOLD is the coordination.** If another AGV holds the lane or the station this AGV
  needs, the AI returns `hold` → wait, re-evaluate in a few seconds. When it clears, the
  AI returns `proceed`. **No agent messages another directly** — they coordinate through
  the shared snapshot, exactly the way real factory automation does.
- Arms run the same pattern: `_arm_ai_gate` in `dispatch_routes.py` asks the AI
  *"load / unload now or wait?"* before each sweep.

### Step 6 — they execute the hardcoded scripts

The AI's `proceed` answer triggers the **next planned script** in the AGV's safety-rail
sequence — and that script is a regular Python function that does the actual work:

- `move_to_input` walks the AGV cell-by-cell to the input dock,
- `load_package` polls the `load_ready` signal until the arm has placed the package,
- `traverse_lane` walks the shared lane to the output dock,
- `unload_package` polls `unload_ready`,
- `return_to_bay` walks the route back home.

Arms execute `_animate_arm` with a hardcoded angle sequence
(`[270, 230, 190, 180, …, 0, …, 270]`). The package appears on the AGV when the arm
finishes the sweep, and the AGV's `carried_object` is set.

### What the browser sees throughout

SocketIO events stream continuously: `agv_update` (token moves), `arm_angle_update`
(arm rotates), `table_update` (packages appear / disappear on tables), `log_message`
(every AI decision + every script step). The Konva canvas eases the AGV down its
route, sweeps the arms 0°↔180°, shows the package travelling from input table → AGV
cargo bed → output table.

---

## The scripts still drive every movement — the AI controls only timing

**Important to land for the audience.** The robots are NOT freelancing. Every concrete
action — going to the input arm, getting loaded, traversing the lane, unloading,
returning to bay — is a **hardcoded Python function**. The AI is the *conductor* that
chooses which scripted action to trigger next, and whether to wait. It cannot invent a
new action.

### The 12 hardcoded AGV scripts

All in `scripts/agv_brain.py`, registered in `SCRIPT_REGISTRY`:

| # | Script | What its code actually does |
|---|---|---|
| 1 | `acquire_input_station` | POST `/agv/station/acquire {station: input_arm}` — blocks until the input arm is free |
| 2 | `move_to_input` | Iterates `route_to_input` cells, POSTs `/agv/<id>/move` one cell at a time |
| 3 | `load_package` | Polls `/agv/<id>/load_ready` every 2 s until signalled, then clears |
| 4 | `release_input_station` | POST release on the input station lock |
| 5 | `acquire_lane` | POST `/agv/lane/acquire` — blocks until the shared lane is free |
| 6 | `traverse_lane` | Iterates the lane cells from input dock to output dock, cell-by-cell moves |
| 7 | `acquire_output_station` | POST acquire on the output station lock |
| 8 | `move_to_output` | Confirms the output-dock position |
| 9 | `unload_package` | Polls `/agv/<id>/unload_ready` until signalled |
| 10 | `release_output_station` | POST release |
| 11 | `release_lane` | POST release on the lane lock |
| 12 | `return_to_bay` | Iterates `route_to_bay` cells back to its bay |

These are deterministic Python functions — they would run identically with no AI at
all. (Arms similarly: `_animate_arm` with a hardcoded angle sequence.)

### What the AI is allowed to say each step

`_decide_next_script` in `agv_brain.py` asks qwen14b one question per step and accepts
**only three answers**:

```text
{"script": "<next planned step>"}  → run the next hardcoded script in the sequence
{"script": "hold"}                  → wait HOLD_SLEEP seconds, then re-evaluate with fresh status
{"script": "fault"}                 → abort the task
```

Anything else the AI returns is **ignored and treated as "proceed"**. The AI physically
cannot pick `unload_package` before `load_package` — the firewall in
`_decide_next_script` only offers the *next planned step* as the "proceed" option.

### One-line takeaway

> **WHAT runs is scripted. WHEN it runs is AI-decided.**

### Concrete evidence (from the live test)

```text
[22:09:46] agv-3  AI decision: HOLD before 'load_package' (lane=agv-1, ...)
[22:09:49] agv-3  AI decision: HOLD before 'load_package' (...)
[22:09:53] agv-3  AI decision: HOLD before 'load_package' (...)
[22:09:57] agv-3  AI decision: run 'load_package'
[22:09:57] agv-3  step load_package (attempt 1/3)
```

The AI did not decide *how* to load. It saw agv-1 was active and said "wait" three
times. When the coordination cleared, it said "go." The loading itself ran the
unchanged hardcoded `_script_load_package` function.

---

## Setup: the TUC server connection

### How the models are reached — the full network journey

The two heavy models run on the TU Clausthal GPU box (`cloud-247`). This machine cannot
reach them directly; the models are exposed through a **persistent reverse SSH tunnel**:

```text
cloud-247 Ollama 127.0.0.1:11434  ──ssh -R──►  this host 127.0.0.1:11500
```

When an AGV brain or the planner makes an AI call, the request travels **four hops out**
to TU Clausthal and four hops back. Every hop is real network — there is no in-process
shortcut:

```text
  [agv-N container]                       e.g. POST /ai/tactical
        │  HTTP over the Docker bridge
        ▼
  [backend container]                     llm_service.tactical()
        │  HTTP →  http://host.docker.internal:11500
        │  (Docker maps "host.docker.internal" to the host gateway IP)
        ▼
  [HOST machine, port 11500]              local end of the SSH tunnel
        │  encrypted SSH tunnel
        │  (VPN provides the route to cloud-247)
        ▼
  [cloud-247 in TU Clausthal, port 11434] remote end of the SSH tunnel
        │  loopback HTTP                  Ollama listening locally
        ▼
  [Ollama on cloud-247]                   /api/generate {model, prompt, …}
        │
        ▼
  [GPU on cloud-247]                      the model actually runs here
                                          qwq:32b ≈ 20 GB VRAM
                                          qwen2.5:14b ≈ 9 GB VRAM

  ▲ response travels the same 4 hops back ▲
```

- `http://127.0.0.1:11500` → real TU Clausthal GPU models (`qwq:32b` + `qwen2.5:14b`)
- `http://ollama:11434`    → local docker CPU container (`qwen2.5:0.5b` — legacy arm brain)

The tunnel auto-starts / reconnects from cloud-247 (`~/revtun.sh` + `@reboot` cron). The
operator connects to TU Clausthal via VPN — note the VPN gives network *reachability*,
the tunnel is a separate persistent SSH process, and **both have to be up** for the AI
to work. (`VPN connected` ≠ `tunnel listening`.)

**Latency budget** the audience should expect:

- *Cold-load* (first call after a model swap): minutes — the GPU is loading 9–20 GB of
  weights from disk into VRAM. `model_swap` events surface this so it is never a
  mystery freeze.
- *Warm call* (model already resident): 4–10 s end-to-end across all 4 hops.

### What it looks like when a hop breaks

Each hop has a distinct failure signature — useful operationally and worth knowing if
the demo misbehaves:

| Hop that fails | What you see | Likely cause |
| --- | --- | --- |
| container → host | `/ai/health` returns 500 or empty | Docker is down, or backend container not running |
| host → `:11500` | `curl 127.0.0.1:11500` → *Connection refused* | SSH tunnel is down (`ss -tlnp \| grep 11500` empty) |
| tunnel → cloud-247 | `/api/tags` returns 502 / timeout | network, VPN, or cloud-247 unreachable |
| cloud-247 → Ollama | `/api/tags` returns 503 | Ollama process down on cloud-247 |
| Ollama → GPU | `/api/generate` returns 500 | GPU OOM or contention |

Any of these → `llm_service` emits a `model_swap` "load FAILED" event AND every agent
falls through to the **deterministic safety rail**. The run still completes — the
system fails closed by design.

### The qwen_brain / llm_service split (important)

`qwen_brain.py` (legacy per-arm 0.5b) and `llm_service.py` (new two-model AI layer) **both
read `OLLAMA_BASE_URL`**. Repointing it globally would break `qwen_brain` (TUC has no 0.5b).
So they are now decoupled:

- `qwen_brain` → `OLLAMA_BASE_URL` = `http://ollama:11434` (local container, unchanged)
- `llm_service` → `LLM_BASE_URL` (precedence: `LLM_BASE_URL` → `OLLAMA_BASE_URL` → tunnel default)

`pipeline.yaml`:

```yaml
ai:
  ollama_base_url: http://ollama:11434           # qwen_brain (legacy 0.5b)
  llm_base_url:   http://host.docker.internal:11500   # llm_service → TUC
  model_planner:  qwq:32b-q4_K_M
  model_tactical: qwen2.5:14b-instruct-q4_K_M
```

`scripts/generate_compose.py` injects `LLM_BASE_URL` into the backend and adds
`extra_hosts: ["host.docker.internal:host-gateway"]` (resolves automatically on Docker
Desktop; **required** on a Linux CI/CD host).

### Run commands

```bash
# 0. Pre-flight: confirm the tunnel + models are reachable from this host
curl -s http://127.0.0.1:11500/api/tags | python -m json.tool
#    → must list qwq:32b-q4_K_M and qwen2.5:14b-instruct-q4_K_M
#    If it fails: tunnel is down. Here: `ss -tlnp | grep 11500`.
#                 On cloud-247: `tail ~/revtun.log`.

# 1. Regenerate compose from pipeline.yaml (never hand-edit docker-compose.yml)
python scripts/generate_compose.py

# 2. Build + start the stack
docker compose up -d --build

# 3. Verify the backend sees the TUC models (NOT the local 0.5b)
curl -s http://localhost:8080/ai/health | python -m json.tool
#    → {"url":"http://host.docker.internal:11500","status":"connected",
#       "planner_present":true,"tactical_present":true, ...}

# 4. Drive it: prompt → master plan → AI-coordinated execution
curl -s -X POST http://localhost:8080/plan \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Sort 3 packages by rainbow color, one per AGV","sync":true}'
#    First call cold-loads qwq:32b (minutes). Then it swaps once to qwen14b
#    for the whole execution run. Watch the AGVs in the browser at :8080.
```

> **Cold-load note.** The shared GPU holds **one model at a time**. The first `/plan`
> cold-loads `qwq:32b` (~minutes). The first agent tactical call swaps once to
> `qwen2.5:14b`, which then stays resident for the whole run. Every swap emits a
> UI-visible `model_swap` event so the load is not a mystery freeze.

---

## Code-level flow (the full path, file by file)

```
[BROWSER]  POST /plan {"prompt": "..."}
   │
   ▼
[ai_routes.py /plan]                              ── app/routes/ai_routes.py
   │  with planning_window():           ← keeps qwq:32b resident, blocks tactical
   │    interpret(prompt)  → TaskSpec
   │    central_brain.make_plan(spec)   → assignments
   │  (planning_window CLOSES here)
   │
   ▼
[llm_service.py]                                  ── app/services/llm_service.py
   │  _model_gateway: ensure qwq:32b in VRAM (one swap, logged model_swap)
   │  POST {LLM_BASE_URL}/api/generate  → TUC qwq:32b
   │
   ▼
[plan_executor.dispatch_plan(plan)]               ── app/services/plan_executor.py
   │  for each assignment (sorted by sequence):
   │    POST http://agv-N:8100/assign  {package_id, agv_id, route, label}
   │    start arm choreography thread (AI-confirmed)
   │
   ▼
[agv_server.py /assign]                           ── scripts/agv_server.py
   │  202 immediately; run_task(assignment) in a daemon thread
   │
   ▼
[agv_brain.run_task]  AI-DRIVEN LOOP              ── scripts/agv_brain.py
   │  while not done (cap MAX_DECISION_ITERS):
   │    det_next = first uncompleted _NORMAL_STEPS entry      ← safety rail
   │    snap     = GET /coord/snapshot                        ← shared status
   │    choice   = _decide_next_script(...)  → POST /ai/tactical
   │                  │
   │                  ▼
   │             [ai_routes /ai/tactical] → llm_service.tactical()
   │                  → TUC qwen2.5:14b → {"script": "proceed|hold|fault"}
   │                  (HTTP 409 if a planning window is open → fallback proceed)
   │    HOLD  → sleep, re-evaluate (this is AGV↔AGV coordination)
   │    FAULT → abort
   │    PROCEED → _run_step(det_next)  (with bounded retry + AI recovery)
   │
   ▼
[backend REST]  /agv/<id>/move, /agv/lane/acquire, /agv/station/acquire …
   │  updates agv_state / grid_state ; emits agv_update via SocketIO
   │
   ▼
[arm choreography]                                ── app/routes/dispatch_routes.py
   │  _arm_ai_gate(arm, agv, "load"|"unload")  → llm_service.tactical()
   │     qwen14b "go" / "wait"  (arm↔AGV coordination)
   │  _animate_arm(...) → arm_angle_update ; table_update ; load_ready/unload_ready
   │
   ▼
[BROWSER]  agv_update / arm_angle_update / table_update / log_message
           → Konva canvas: AGV tokens travel, arms sweep, packages move
```

### The three AI roles

| Layer | Model | Where | Decides |
|---|---|---|---|
| **Interpreter** | qwq:32b | `app/agents/interpreter.py` | prompt → TaskSpec (intent, count, ordering) |
| **Master planner** | qwq:32b | `app/agents/central_brain.py` | TaskSpec → per-AGV/arm assignments + sequence |
| **Tactical (per AGV)** | qwen2.5:14b | `scripts/agv_brain.py` → `/ai/tactical` | proceed / hold / fault, every step, from shared status |
| **Tactical (per arm)** | qwen2.5:14b | `dispatch_routes._arm_ai_gate` | load / unload now, or wait |

### The shared-status channel

`GET /coord/snapshot` (`app/routes/agv_routes.py`) returns:

```json
{ "agvs": {"agv-1": {"cell","status","carried_object"}, ...},
  "lane_holder": "agv-2"|null,
  "station_holders": {"input_arm": "agv-1"|null, "output_arm": null},
  "arms": {...}, "assignments": [...] }
```

No agent messages another directly. Each agent's tactical AI reads this snapshot and
reasons about what the others are doing — that is how AGV↔AGV and AGV↔arm "coordinate".

### Safety guarantees (why it can't run away)

- **Python owns sequence integrity.** The AI chooses pacing (proceed/hold/fault); it
  cannot reorder or skip the `_NORMAL_STEPS` safety rail.
- **Deterministic fallback.** Any 409 (planner running) / timeout / invalid reply →
  proceed with the deterministic next step. The run never deadlocks on the model.
- **Caps.** `MAX_DECISION_ITERS` + `HOLD_SLEEP` stop a permanent hold from looping forever.
- **VRAM guard.** One model resident at a time; planning window blocks tactical (409);
  one swap per phase; reassignment is deterministic (zero model calls).

---

## Files changed today

### New

| File | What |
|---|---|
| `app/services/plan_executor.py` | `dispatch_plan()` — delivers the master plan to AGV containers + starts arm choreography. **Closes the planner→agents gap.** |
| `documentation/(20 May 2026) AI Integration.md` | This document. |

### Modified — backend

| File | Change |
|---|---|
| `app/services/llm_service.py` | `BASE_URL` precedence `LLM_BASE_URL` → `OLLAMA_BASE_URL` → tunnel default (decoupled from qwen_brain). |
| `app/routes/agv_routes.py` | New `GET /coord/snapshot` (shared status); imports `arm_state`. |
| `app/routes/ai_routes.py` | `/plan` calls `plan_executor.dispatch_plan()` after the planning window closes. |
| `app/routes/dispatch_routes.py` | `_arm_ai_confirm` / `_arm_ai_gate`; input + output arm gated on qwen14b before sweeping. Dock-cell polling (earlier fix). |
| `pipeline.yaml` | `ai.llm_base_url`; station `dock` cells `[2,1]`/`[11,1]`; lane between docks. |
| `scripts/generate_compose.py` | Injects `LLM_BASE_URL` + `extra_hosts: host.docker.internal:host-gateway` on backend. |
| `app/models/pipeline_config.py` | `get_warehouse_scene()` emits `dock` + `dock_px` per station. |

### Modified — agents / brains

| File | Change |
|---|---|
| `scripts/agv_brain.py` | `run_task` rewritten as the AI decision loop (`_coord_snapshot`, `_decide_next_script`, `_run_step`); routes to dock cells; path-follows back to bay; `MOVE_STEP_SLEEP` tuned. |

### Modified — frontend / infra

| File | Change |
|---|---|
| `docker/nginx.conf` | Added `coord` to the proxied-prefix regex. |
| `frontend/src/runtime/socketDriver.ts` | AGV eases toward target (no cell snap); tracking speed tuned so the visual matches the logical cell. |
| `frontend/src/canvas/overlay/KonvaOverlay.tsx` | `ArmLayer`: two-link arm reaching the real dock; table mirrored; dock zone at the real AGV cell. |
| `frontend/src/canvas/overlay/AgvTokenLayer.tsx` | Vehicle shape (chassis + wheels + heading) rotated to travel; visible cargo box. |
| `frontend/src/canvas/labels/LabelsOverlay.tsx` | Stale "Decentralised negotiation" prototype labels removed (no-op). |

---

## Verification (what was proven live)

```bash
# Layer 1 — master AI plan (qwq:32b)
curl -s -X POST localhost:8080/plan -d '{"prompt":"Sort 3 packages by rainbow color, one per AGV","sync":true}' -H 'Content-Type: application/json'
# → _model:qwq:32b ; assignments distributed agv-2(red) agv-3(orange) agv-1(yellow)
#   execution:{status:dispatched, dispatched:[3 AGVs], skipped:[]}   ← gap closed

# Layer 2 — per-AGV tactical (qwen2.5:14b), AI-driven every step
docker logs interact-agv-1 2>&1 | grep "AI decision"
# → AI decision: run 'traverse_lane' / 'acquire_output_station' / ... / task complete

# AI COORDINATION (the proof) — agv-3 waited for agv-1 by its own AI decision
docker logs interact-agv-3 2>&1 | grep -E "HOLD|load_package"
# → AI decision: HOLD before 'load_package' (lane=agv-1, ...)   ×3
#   AI decision: run 'load_package'        ← proceeded when coordination cleared

# Live shared status
curl -s localhost:8080/coord/snapshot | python -m json.tool
```

Result: `qwq:32b` planned and distributed 3 packages one-per-AGV in rainbow order; the
plan reached the containers; each AGV's `qwen2.5:14b` decided every step; **agv-3's AI
chose to HOLD three times while agv-1 was active, then proceeded when clear** — real
AI-driven collision avoidance; agv-1 completed its full cycle.

---

## Honest trade-off

The AI is now consulted before **every** script, serialized through one shared
`qwen2.5:14b`. A 3-AGV run does ~40+ serialized model calls plus two GPU cold-loads, so a
full run is **minutes-slow**. This is inherent to "AI decides every step" on a single
shared GPU — not a bug. The deterministic safety rail keeps it correct and unstuck
throughout.

**Speed lever (future toggle):** consult the AI only at *coordination decision points*
(station/lane entry, fault) and let routine moves run deterministically — a config flag,
not a redesign.

---

## Known gaps (carried forward)

- Arms still use `dispatch_routes` simulated choreography (no real arm containers); the AI
  gate confirms timing but the arm motion is animated, not physical.
- `central_brain.reassign()` is deterministic and handles single-AGV fault; whole-plan
  re-planning on cascading faults is not wired.
- Mid-run replan is disabled by default (`ALLOW_MIDRUN_REPLAN=0`) to protect the VRAM
  budget — re-enabling needs a tested swap-back path.
- Run latency (above) — acceptable for a demo, needs the decision-point toggle for scale.

---

## Roadmap — what is still in the pipeline

Stakeholder view of where this goes next. Each item is concrete and scoped.

### Next sprint (~1–2 weeks)

| Item | Why it matters | Effort |
|---|---|---|
| **Decision-point toggle** — `AI_DECISION_MODE = every_step \| coordination_points` | Production throughput. "Every step" is the demo behaviour (minutes per run); "coordination points" (station/lane entry + fault only) drops a 3-AGV run to ~30 s with the same AI safety properties. Same code, one config flag. | Small |
| **Demo warm-up endpoint** `POST /ai/warmup` | Eliminates the multi-minute cold-load surprise during a live demo. Pre-loads `qwq:32b` then swaps to `qwen14b` so the first real prompt runs warm. | Small |
| **AI-layer test pass in CI** | `tests/test_llm_service.py`, `tests/test_central_brain.py`, `tests/test_plan_executor.py` scaffolded but not green in CI. | Medium |
| **Tunnel auto-recovery** | If `/api/tags` preflight fails mid-run, retry-with-backoff + UI banner; today the run falls through to the deterministic safety rail silently. | Small |

### Next month

| Item | Why it matters | Effort |
|---|---|---|
| **Real arm containers** | Today arms are animated by `dispatch_routes._animate_arm`; replace with the `arm-N` container's `/sequence` endpoint (already exists from the dev-sneh stack) gated by the same `_arm_ai_gate`. Removes the "simulated, not physical" caveat. | Medium |
| **Whole-plan replan on cascading fault** | `central_brain.reassign()` only replaces one AGV. When ≥2 AGVs fault, re-run the planner with reduced capacity (one-shot, surfaced as a UI event because of the qwq:32b cold-load). | Medium |
| **Controlled mid-run replan** | Enable `ALLOW_MIDRUN_REPLAN=1` behind a UI button so the operator can request a re-plan; honest about the cold-load it triggers. | Small |
| **GPU metric panel** | `/ai/health` reports `loaded_model`; add per-call latency, queue depth, swap count to the inspector. Closes the observability gap. | Medium |

### Future (research / scale)

| Item | Why it matters | Effort |
|---|---|---|
| **Dedicated GPU per layer** | Run qwq:32b and qwen14b on different GPUs / Ollama instances. Removes the single-model-resident constraint → tactical and planning overlap → real concurrency. | Large |
| **Confidence-gated AI** | Use tactical-model logprobs / a calibration layer to fall back to the deterministic rail when AI confidence is low. Reduces wasted GPU on obvious decisions. | Large |
| **Agent negotiation protocol** | Today coordination is via shared snapshot. A short JSON message protocol (subscribe-capable bus, not emit-only) would let agents *ask* each other before deciding. | Large |
| **Model trade study** | qwq:32b vs qwen2.5:14b for planning; qwen2.5:14b vs qwen2.5:7b for tactical. Benchmark accuracy × latency × GPU. | Medium |

---

## Hardening status (for the professor / company)

| Concern | Today | Status |
|---|---|---|
| **AI output validation (firewall)** | `agv_brain._decide_next_script` accepts only `{det_next, hold, fault}`; anything else → `det_next`. Planner output validated by `app/services/plan_validator.py` (whitelisted AGV ids, sequence integrity, route shape). Defensive `json.loads` on tactical replies. | ✅ implemented |
| **Prompt-injection surface** | Only the top-level user prompt to `/interpret` and `/plan` reaches the LLM; AGV/arm tactical prompts are backend-built strings (status snapshot + fixed system). User cannot inject into tactical. | ✅ implemented |
| **Fail-closed on AI down** | Tunnel 5xx / timeout / HTTP 409 → deterministic safety rail (`_NORMAL_STEPS`); `model_swap` event surfaces every load. Run never deadlocks on the model. | ✅ implemented |
| **VRAM guard** | `_model_gateway` serializes swaps; `planning_window` blocks tactical (409); reassignment is deterministic (zero model calls). | ✅ implemented |
| **AI unit tests passing in CI** | `tests/test_llm_service.py`, `tests/test_central_brain.py`, `tests/test_interpreter.py` exist — not green in CI yet. | 🟡 next sprint |
| **End-to-end integration test** | Manual verification today (logs + curl). No automated `compose up → /plan → assert` test. | 🟡 next sprint |
| **Tunnel auto-recovery** | Preflight check on `/ai/health`; no auto-retry on 5xx during a run. | 🔴 next sprint |
| **Secrets / auth on the tunnel** | SSH reverse-tunnel is the auth boundary; no app-level token on `/api/generate`. | 🟡 acceptable on the closed network; revisit on exposure |

---

## Demo runbook (so the cold-load doesn't bite you live)

The first call after a backend restart cold-loads `qwq:32b` on the shared GPU — a multi-minute silent wait that will look like a freeze on a projector. **Always warm the models before the audience arrives.**

### Pre-flight (10 min before the demo)

```bash
# 0. Tunnel + models reachable
curl -s http://127.0.0.1:11500/api/tags | python -c "import sys,json;m=[x['name'] for x in json.load(sys.stdin)['models']];print('TUC OK,',m)"
#    expect both qwq:32b-q4_K_M and qwen2.5:14b-instruct-q4_K_M

# 1. Backend sees TUC
curl -s http://localhost:8080/ai/health | python -m json.tool
#    status:"connected", planner_present:true, tactical_present:true

# 2. WARM the planner — sends a tiny prompt so qwq:32b cold-loads NOW, not on stage.
#    Takes 2–5 min the first time; nothing to do but wait.
curl -s -m 600 -X POST http://localhost:8080/plan \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Sort 1 package, one per AGV","sync":true}' \
  | python -c "import sys,json;d=json.load(sys.stdin);print('planner warm —', d.get('status'), d.get('_model'))"

# 3. Wait for the AGVs to return to bays and idle, then confirm
curl -s http://localhost:8080/coord/snapshot \
  | python -c "import sys,json;d=json.load(sys.stdin);print({k:v['status'] for k,v in d['agvs'].items()})"
#    all 'idle'

# 4. /ai/health should now show the execution model resident
curl -s http://localhost:8080/ai/health | python -m json.tool | grep loaded_model
#    loaded_model:"qwen2.5:14b-instruct-q4_K_M"
```

### Live flow

Open **two windows side by side**:

1. Browser at `http://localhost:8080` (the canvas).
2. Terminal: `docker compose logs -f agv-1 agv-2 agv-3 | grep -E "AI decision|task start|task complete"`.

```text
[1] Type into the prompt bar:  Sort 3 packages by rainbow color, one per AGV
[2] POST /plan fires (warm — seconds, not minutes).
    The log window prints lines like:
        agv-2  task start: pkg=pkg-1 label=red
        agv-1  AI decision: run 'acquire_input_station'
        agv-3  AI decision: HOLD before 'load_package' (lane=agv-1, ...)
        agv-3  AI decision: run 'load_package'
[3] Point at the canvas: AGVs leave bays, dock at input one at a time, arm sweeps,
    package appears on the AGV, AGV travels the lane, docks at output, arm sweeps,
    package delivered, AGV returns to bay.
[4] Point at a HOLD line: "this AGV's qwen14b read the shared status, saw another
    AGV held the station, and chose to wait — AGV-to-AGV coordination via AI,
    not a hard-coded delay."
```

### Talking-point map

| What appears | What to say |
|---|---|
| `task start: pkg=pkg-1 label=red` | "Master AI (`qwq:32b`) picked which AGV gets which package, in what rainbow order — the assignment just landed in the AGV container." |
| `AI decision: run '<script>'` | "The AGV's tactical AI (`qwen2.5:14b`) decided to advance — based on its live view of the other AGVs." |
| `AI decision: HOLD before '<step>' (lane=agv-1, ...)` | "**This is the coordination moment.** Saw another AGV held the resource, chose to wait. No collision, decided by the model." |
| `arm-N AI: holding <load\|unload> for agv-N` | "The arm's tactical brain confirms the AGV is ready before the sweep — arm↔AGV handshake via AI." |
| `task complete: pkg=pkg-N` | "Full bay→input→lane→output→bay cycle, every step chosen by the AI under safety-rail supervision." |

### If something goes wrong on stage

- **Models cold (forgot to warm)** — say *"the GPU is cold-loading the 32B model now; this is the documented `model_swap` event"* and show `/ai/health` in a side terminal. The wait is legitimate, not a bug.
- **Tunnel down mid-demo** — the run falls through to the deterministic safety rail and still completes. Say *"this is the fail-closed behaviour — the deterministic sequence keeps the work flowing even if the AI link drops."*
- **AGV faults** — `central_brain.reassign()` swaps to the next idle AGV deterministically. Point at the `reassignment` log line.

---

## So: will the doc + demo clear the concerns?

- **Yes** for: architecture, correctness evidence, honest limitations, scaling plan, hardening posture, where it goes next.
- **Conditional yes** for the demo itself — only if you run the **pre-flight warm-up** above. Without it, the audience watches a multi-minute cold-load that looks like a freeze.
- **Open questions you should be ready for** (all addressed by sections above): test status (next-sprint), real arms vs simulated (next-month), tunnel auto-recovery (next-sprint), throughput at scale (decision-point toggle, next-sprint).
