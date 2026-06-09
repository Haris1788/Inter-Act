# AI and without AI — how the system works (20 May 2026)

> **Gitignored personal reference.** Captures the answer to a recurring question:
> *"When AI is unavailable, what is actually making decisions for the AGVs and arms?"*
> Read this when you're confused about which layer is doing what.

---

## The short answer

There are **two completely separate coordination mechanisms** in the system, and one
is a graceful fallback for the other:

| Mechanism | Active when AI is up | Active when AI is down |
| --- | --- | --- |
| **AI tactica$t = @(
 "Doc-check\tuc-setup-and-run.md.bak",
 "scripts\arm_server.py.bak","scripts\agv_server.py.bak","scripts\agv_server.py.bak2",
 "scripts\agv_bus.py.bak","scripts\agv_bus.py.bak2","scripts\agv_bus.py.bak.s3c",
 "scripts\arm_bus.py.bak.s3b","scripts\generate_compose.py.bak",
 "app\routes\agv_routes.py.bak","app\routes\dispatch_routes.py.bak2","app\routes\dispatch_routes.py.bak.s3b",
 "app\services\task_spec.py.bak.s3b","app\services\prompt_builder.py.bak.s3b","app\services\container_manager.py.bak",
 "app\agents\master_announcer.py.bak","app\agents\master_announcer.py.bak3","app\agents\master_announcer.py.bak.s3b","app\agents\master_announcer.py.bak.s3c",
 "docker-compose.yml.bak","pipeline.yaml.broken.bak","pipeline.yaml.scenario-baseline.bak",
 "scripts\arm_1_script.py","scripts\arm_2_script.py","scripts\arm_3_script.py",
 "tests\test_qwen_behavior.py","tests\test_qwen_comprehensive.py","qwen_behavior.yaml",
 "app\templates\index.html")
foreach ($f in $t) { if (Test-Path $f) { Remove-Item -LiteralPath $f -Force; "deleted: $f" } }
if (Test-Path "app\static") { Remove-Item "app\static" -Recurse -Force; "deleted: app\static\" }
l HOLD / proceed** (qwen14b) | ✓ primary — decides timing & coordination from shared status | (fall-through — firewall returns "proceed") |
| **Lock blocking** (`LaneLock`) | ✓ defence in depth — rarely actually blocks | ✓ this is what avoids collisions today |
| **Deterministic step sequence** (`_NORMAL_STEPS`) | ✓ safety rail — AI can only choose proceed or hold | ✓ this is what runs, in order |
| **Hardcoded scripts** (move, load, traverse, …) | ✓ scripts always do the actual work | ✓ same scripts, same behaviour |

Whether the AI is up or down, **the scripts do the work**. The only thing the AI does
is decide *when* the next script runs and whether to hold to avoid a collision. When AI
is unavailable, the lock primitives + the fixed step order do that coordination instead.

---

## The decision stack — bottom to top

| Layer | What decides | When AI is unavailable |
| --- | --- | --- |
| 1 — **Dispatch endpoint** (`dispatch_routes.dispatch`) | Goal text → package count + which idle AGVs are assigned | Regex parses a number; picks idle AGVs in order |
| 2 — **AGV brain loop** (`agv_brain.run_task`) | Which script to run next | Falls through to `det_next` — next step in `_NORMAL_STEPS` |
| 3 — **Individual scripts** (`move_to_input`, `load_package`, …) | Cell-by-cell path; when to wait; when to release | Hardcoded Python — no AI was ever inside scripts |
| 4 — **Lock primitives** (`LaneLock` in `grid_state.py`) | "Is this AGV allowed on the lane / at the station right now?" | `threading.Condition` — first to ask gets it; others block until release |
| 5 — **Arm choreography** (`_auto_arm_signals`) | When to sweep; what angles | Hardcoded angle list; polls AGV cell to know arrival |
| 6 — **Frontend canvas** | (Doesn't decide — purely reactive) | Renders SocketIO events |

---

## Layer 1 — Dispatch endpoint (the entry point)

`app/routes/dispatch_routes.py:dispatch()`. Pure Python, three decisions:

```text
1. "How many packages?"   ── _parse_package_count(text): regex 1–10, clamped to 1–3
2. "Which AGVs?"           ── _idle_agvs(): filters agv_state for status=="idle"
3. "What route?"           ── hardcoded ["bay", "input", "output", "bay"]
```

Labels and colours rotate through fixed lists (`["red","blue","green"]` etc.). No
reasoning, no priority — just iteration order.

For `"send 2 packages"`: 2 packages assigned to the first 2 idle AGVs, routes
hardcoded.

> **Note** — the AI path (`POST /plan`) bypasses this layer entirely. There the
> Master AI (qwq:32b) makes these decisions instead. Dispatch is the AI-free entry.

---

## Layer 2 — AGV brain loop (where AI plugs in)

`scripts/agv_brain.py:run_task()`. Each AGV container receives its assignment from
Layer 1 (or from `plan_executor` if it came through `/plan`) and runs:

```text
loop:
    det_next = first not-yet-completed _NORMAL_STEPS entry
    snap     = GET /coord/snapshot
    choice   = _decide_next_script(...)   → POST /ai/tactical

    AI succeeds → choice ∈ { det_next , "hold" , "fault" }
    AI fails    → firewall returns det_next        ←  THIS is the fall-through

    if choice == det_next:   run that script, mark completed
    if choice == "hold":     sleep HOLD_SLEEP, re-evaluate with fresh snapshot
    if choice == "fault":    abort
```

`_NORMAL_STEPS` is a fixed list of 12 step names, in order. The AI can choose
`det_next` / `hold` / `fault` — it **cannot** reorder, skip, or invent steps.

The 12 steps:

```text
 1. acquire_input_station     ←  block until input arm is free
 2. move_to_input             ←  cell-by-cell to dock cell [2,1]
 3. load_package              ←  wait for input arm's load_ready signal
 4. release_input_station
 5. acquire_lane              ←  block until the shared travel lane is free
 6. traverse_lane             ←  cell-by-cell along row 1 dock → dock
 7. acquire_output_station    ←  block until output arm is free
 8. move_to_output            ←  confirm dock at [11,1]
 9. unload_package            ←  wait for output arm's unload_ready signal
10. release_output_station
11. release_lane
12. return_to_bay             ←  cell-by-cell back to the home bay
```

When AI is down, the brain marches 1 → 12 for every assignment.

---

## Layer 3 — What each script actually does

These are regular Python functions in `agv_brain.py`. Two patterns.

### Movement scripts (`move_to_input`, `traverse_lane`, `move_to_output`, `return_to_bay`)

```python
for cell in ctx["route_to_input"]:        # path from _straight_path()
    _post(f"/agv/{AGV_ID}/move", {"cell": cell, "status": "moving"})
    time.sleep(MOVE_STEP_SLEEP)            # 0.5 s between cell hops
```

The path is produced by `_straight_path(start, end)` — rectilinear, columns first
then rows. From bay `[10,3]` to input dock `[2,1]`:
`[10,3]→[9,3]→…→[2,3]→[2,2]→[2,1]`. No pathfinder; just go horizontal then vertical.

### Wait scripts (`load_package`, `unload_package`)

```python
deadline = time.time() + LOAD_SIGNAL_TIMEOUT       # 120 s
while time.time() < deadline:
    res = _get(f"/agv/{AGV_ID}/load_ready")
    if res and res.get("ready"):
        return True
    time.sleep(2.0)
```

Pure polling — the AGV does nothing until the arm fires the signal.

### Lock scripts (`acquire_input_station`, `acquire_lane`, `acquire_output_station`)

Each calls the backend's lock-acquire endpoint, which blocks until the lock is free
(see Layer 4).

---

## Layer 4 — How AGVs avoid collisions without AI

`app/models/grid_state.py:LaneLock` is a `threading.Condition` wrapper:

```text
acquire(agv_id, timeout, cancel_check):
    with self._cond:
        while self._holder is not None:    ← someone else has it
            self._cond.wait(timeout=2.0)    ← block until released
        self._holder = agv_id               ← now I hold it
        return True

release(agv_id):
    with self._cond:
        self._holder = None
        self._cond.notify_all()             ← wake the waiters
```

There are three of these lock instances:

- `lane()` — the shared travel lane (only one AGV may be on row 1 between docks)
- `station_lock("input_arm")` — only one AGV at the input dock at a time
- `station_lock("output_arm")` — only one AGV at the output dock at a time

When AI is down, collision avoidance still works — it's just blocking I/O instead
of an AI saying *"HOLD"*. The AGV brain calls `acquire_lane` (Layer 2 → Layer 3
→ backend `/agv/lane/acquire`), and the lock primitive (Layer 4) makes it wait
until another AGV releases.

---

## Layer 5 — The arm choreography (an independent thread)

`app/routes/dispatch_routes.py:_auto_arm_signals()` runs as a daemon thread per
package, in parallel with the AGV brain. Its decisions:

```text
1. emit table_update            ←  show package on input table
2. poll agv_state until agv reaches [2,1]
3. (AI gate tries here → fails → proceeds)
4. _animate_arm(arm_id, [270, 230, ..., 0, ..., 270])   ←  hardcoded angle list
5. emit load_ready              ←  AGV brain's load_package wakes up
6. emit table_update []         ←  clear input table
7. poll agv_state until agv reaches [11,1]
8. (AI gate tries here → fails → proceeds)
9. _animate_arm(arm_id, [270, 230, ..., 0, ..., 270])
10. emit table_update on output table
11. emit unload_ready
```

The arm has no brain of its own beyond this thread. Without AI, the timing is
purely: *"AGV arrived at dock cell? Sweep, fire signal."*

---

## Layer 6 — The frontend (purely reactive)

The Konva canvas listens for SocketIO events and animates:

| Event | What the canvas does |
| --- | --- |
| `agv_update` | Ease the AGV token to the new cell |
| `arm_angle_update` | Rotate the arm |
| `table_update` | Show / hide packages on the input or output table |
| `log_message` | Append a line to the log panel |

The frontend never decides anything — it just reflects the backend's state.

---

## Concrete trace — two AGVs through Dispatch, no AI

Click Dispatch with *"send 2 packages"*. Here's what happens, second by second:

```text
T+0.0 s   dispatch_routes: package count = 2, idle AGVs = [agv-1, agv-2]
          POST /assign to agv-1 and agv-2 (0.4 s apart)
          spawn _auto_arm_signals thread for each package

T+0.5 s   agv-1.run_task starts
          step acquire_input_station → station_lock("input_arm").acquire() → granted
          step move_to_input → cell-by-cell from [10,3] toward [2,1]

T+0.9 s   agv-2.run_task starts
          step acquire_input_station → station_lock("input_arm").acquire() → BLOCKS
          (agv-1 holds it; agv-2 waits in threading.Condition.wait())
          ←  THIS is collision avoidance without AI

T+5 s     agv-1 arrives at [2,1] (input dock)
          step load_package → polls /load_ready every 2 s

T+5 s     _auto_arm_signals (for pkg-1) sees agv-1.cell == [2,1]
          tries _arm_ai_gate → fails (TUC down) → proceeds
          _animate_arm(1, [270, 230, ..., 0, ..., 270])  ~3.3 s

T+8.3 s   _auto_arm_signals fires load_ready
          agv-1.load_package returns ✓
          step release_input_station → station_lock releases → agv-2 unblocks

T+8.4 s   agv-2.acquire_input_station now succeeds
          step move_to_input → cell-by-cell

T+8.5 s   agv-1.acquire_lane → lane().acquire() → granted
          step traverse_lane → cells along row 1 from [2,1] to [11,1]

T+14 s    agv-2 arrives at [2,1], starts load_package polling
          meanwhile agv-1 reaches [11,1], acquire_output_station → granted
          load/unload arm dance for agv-1 plays out
          ...
```

Notice: in the trace there are **exactly zero AI decisions**. The coordination
still works because the locks block at the right moments, and the polling
handshakes (`load_ready`, `unload_ready`) sync the arms with the AGVs.

---

## How this looks in the logs

### With AI down (today, Dispatch)

```text
agv-1  step acquire_input_station (attempt 1/3)
agv-1  step move_to_input (attempt 1/3)
agv-1  step load_package (attempt 1/3)
agv-1  AI recovery advice after <step> failure: hold   ←  AI fallback timed out
agv-1  step release_input_station (attempt 1/3)
```

No `AI decision: run '<script>'` lines — those only appear when the *positive*
tactical reply comes back. With TUC down the brain takes the silent fallback path.

### With AI up (yesterday, /plan)

```text
agv-2  task start: pkg=pkg-1 label=red
agv-1  AI decision: run 'acquire_input_station'
agv-1  step acquire_input_station (attempt 1/3)
agv-1  AI decision: run 'move_to_input'
agv-3  AI decision: HOLD before 'load_package' (lane=agv-1, ...)   ←  the coordination moment
agv-3  AI decision: HOLD before 'load_package' (lane=agv-1, ...)
agv-3  AI decision: run 'load_package'                              ←  proceeded when clear
agv-1  task complete: pkg=pkg-3
```

Every step is announced before it runs — that announcement is the AI.

---

## The two paths summarised

| What you click | Calls `/plan`? | AI inference actually happens? | AGVs move? |
| --- | --- | --- | --- |
| **AI Plan** (UI button — new today) | ✓ | only if TUC is up | only if TUC is up (plan never produced otherwise) |
| **Dispatch** (UI button — original) | ✗ | if TUC is up, brain's tactical calls land; if down, fall-through | ✓ always — brain falls through deterministic safety rail |

### Why AI Plan fails completely when TUC is down

The `/plan` route NEEDS qwq:32b to produce assignments. With TUC unreachable, the
interpret call fails, `plan_executor.dispatch_plan()` is never reached, and **no
`/assign` POSTs go to the AGV containers — nothing moves**. Honest, fail-closed at
the planning layer.

### Why Dispatch keeps working when TUC is down

The `/demo/dispatch` route is pure Python — it directly POSTs `/assign` to AGV
containers. The AGV brain *tries* to call AI per step, fails, falls back to
deterministic `_NORMAL_STEPS`, and completes the cycle using lock-based
coordination. Same with `_arm_ai_gate` for the arms.

---

## The takeaway

The AI-driven decisions are **additive intelligence** over a deterministic,
lock-coordinated baseline. The baseline alone is enough to keep the warehouse
functioning correctly; the AI is the layer that makes that coordination
*smarter*, *faster*, and *visible* — every HOLD decision is logged and announces
intent before action.

When AI is alive: qwen14b reads the snapshot and chooses HOLD *before* even
calling `acquire_lane`, so the lock rarely actually blocks (it's defence in depth).

When AI is down: the locks step up and do all the work — the fail-closed property
the architecture was designed to provide.
