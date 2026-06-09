# Work Flow — 29 May 2026

**Branch:** `dev-haris` · **US 32 Decentralized AGV Coordination** · **Mode flag:** `COORDINATION_MODE=decentralized` · **Models:** qwq:32b (planner/interpreter) + qwen2.5:14b-instruct (tactical), TU Clausthal GPU via SSH tunnel `host.docker.internal:11500`.

The full path from a natural-language prompt to three delivered packages, with every step labelled by who decides (AI or script), what is emitted on the message bus, and where contract-net + spread fire. Companion to `docs/decentralized-multi-agent-coordination-(27 May 2026).md` (which holds the architecture write-up and the live transcripts).

---

## Phase A — Master interprets and plans (qwq:32b, single warm load)

| # | What happens | Who decides / How | Bus emission |
|---|---|---|---|
| 1 | User types prompt in UI → frontend `POST /plan` | UI (script) | — |
| 2 | Backend opens a `planning_window`, loads `qwq:32b` if cold (one-time per session, minutes the first time) | none — GPU model load | — |
| 3 | **Interpret** prompt → structured TaskSpec (intent, package count, ordering) | **qwq:32b (master)** — natural-language understanding | — |
| 4 | **Plan** → generate `assignments[]` with agv_hint + label + sequence per package | **qwq:32b (master)** — same warm load, no swap | — |
| 5 | Planning window closes, GPU swaps once from qwq:32b → qwen14b for the rest of the run | none — model swap | — |

## Phase B — Contract-net allocation (this is where AGVs decide who runs what)

| # | What happens | Who decides / How | Bus emission |
|---|---|---|---|
| 6 | Master hands plan to `MasterAnnouncer.announce_plan()` | script (Python) | — |
| 7 | Master publishes one announce per package; planner's agv_id is downgraded to `payload.agv_hint` | script (publish_local) — hardcoded | **`task_announce` ×N** |
| 8 | Each AGV's `BusListener._bus_loop` polls `GET /bus/events` every 1 s, receives the announces | script (HTTP polling) | — |
| 9 | Each AGV's `_on_announce` spawns a thread per package (so a slow GPU doesn't block other tasks) | script (threading) | — |
| 10 | Each AGV's thread evaluates: my position, my cargo, my idle/busy state, others' state | **qwen14b @ AGV-N** — outputs `score`, `rationale`, `eta_s` | — |
| 11 | Each AGV publishes its bid on the bus | script (publish) — payload carries qwen14b output verbatim | **`bid` ×M** (one per AGV per task) |
| 12 | Master's `on_bid` subscriber receives each bid, extends task deadline by 6 s (`BID_QUIET_S`) | script (Python, no AI) | — |
| 13 | When peers fall silent for 6 s, master's `_tick` resolves each task | script (deterministic) — **contract-net awarder** | — |
| 14 | Award rule: `max(score)` → tie-break by ord-sum → **spread set** prefers an un-loaded bidder | script (`_assigned_agvs` set) — **spread extension to contract-net** | — |
| 15 | Master publishes one claim per task | script (publish_local) — hardcoded | **`claim` ×N** with `winner` + assignment |
| 16 | Master also POSTs to `/agv/<winner>/assign` (the "kickoff") | script | — |
| 17 | If `/assign` returns 409 busy → master `_reannounce` with `payload.retry = N`, peers clear stale bids and re-bid (capped at 2 retries) | script — **re-announce path** | **`task_announce` (retry)** if triggered |

## Phase C — Winning AGV reaches input arm + loads

| # | What happens | Who decides / How | Bus emission |
|---|---|---|---|
| 18 | AGV's `_on_claim` triggers `_take_task` → `run_task` runs in daemon thread | script | **`agent_status status=running`** |
| 19 | AGV `_decide_next_script` asks "should I do `acquire_input_station` now, hold, or fault?" reading `/coord/snapshot` | **qwen14b @ AGV-N** — judges proceed/hold/fault | — |
| 20 | AGV acquires the input station lock (one AGV at the dock at a time) | script (Python Condition) | — |
| 21 | AGV publishes intent before walking | script — hardcoded string | **`intent action=move_to_input`** |
| 22 | AGV walks cell-by-cell along `route_to_input` | script — deterministic loop | (cell `agv_update` SocketIO events for canvas) |
| 23 | On the LAST cell of the route, AGV signals approach | script — hardcoded payload | **`arm_approach arm_station=input_arm`** |
| 24 | Arm decides "load now or wait?" — `_arm_ai_gate(arm-1, agv-N, "load")` | **qwen14b @ arm-1** | — |
| 25 | If AI says go, arm sweeps fixed angles `[270, 230, 190, 180, … 0 … 270]`, package appears on AGV | script — `_animate_arm` hardcoded | (`arm_angle_update` SocketIO for canvas) |
| 26 | Arm tells the bus the load is done | script — hardcoded payload | **`arm_ready action=loaded for_agv=N`** |
| 27 | AGV `_decide_next_script` asks "what's next?" — sees lane, decides | **qwen14b @ AGV-N** | — |

## Phase D — Lane traversal (shared resource between input and output)

| # | What happens | Who decides / How | Bus emission |
|---|---|---|---|
| 28 | AGV publishes its intent | script — hardcoded | **`intent action=acquire_lane`** |
| 29 | AGV requests the shared lane lock (only one AGV in the corridor at a time) | script (LaneLock — Python Condition) | — |
| 30 | If lock is busy and lock acquire fails, AGV publishes yield (currently rare — locks usually succeed) | script — hardcoded | **`yield`** (only on failure) |
| 31 | AGV traverses lane cells | script — deterministic | — |
| 32 | While running, every 3 s the heartbeat thread emits liveness | script (`_publish_heartbeat_loop`) | **`agent_status status=running`** (continuous) |
| 33 | Other AGVs' `_scan_for_faults` reads peer last-seen; would orphan if > 8 s silent | script — bus-only heartbeat | (would publish `task_orphan` on stale) |

## Phase E — AGV reaches output arm + unloads

| # | What happens | Who decides / How | Bus emission |
|---|---|---|---|
| 34 | AGV decides to go to output | **qwen14b @ AGV-N** — `_decide_next_script` | — |
| 35 | AGV publishes intent | script | **`intent action=move_to_output`** |
| 36 | AGV walks the short route to output dock | script — deterministic | — |
| 37 | On the LAST cell, AGV signals approach | script — hardcoded | **`arm_approach arm_station=output_arm`** |
| 38 | Output arm decides "unload now?" | **qwen14b @ arm-2** — `_arm_ai_gate` | — |
| 39 | Arm sweeps, package appears on output table | script — `_animate_arm` hardcoded | (`arm_angle_update`, `table_update`) |
| 40 | Arm tells the bus | script — hardcoded payload | **`arm_ready action=unloaded for_agv=N`** |

## Phase F — Return + completion

| # | What happens | Who decides / How | Bus emission |
|---|---|---|---|
| 41 | AGV decides "return to bay?" | **qwen14b @ AGV-N** — `_decide_next_script` | — |
| 42 | AGV releases output station + lane locks | script | — |
| 43 | AGV walks `route_to_bay` cell-by-cell | script — deterministic | — |
| 44 | Brain exits `run_task`, status flips to done | script | **`agent_status status=done`** |
| 45 | Master's resolver finishes its tracking; concurrent AGVs may still be mid-cycle | script | — |

## Background (always-on while an AGV is running)

| What | Cadence | Who | Bus emission |
|---|---|---|---|
| Self-heartbeat publish | every 3 s while running | each AGV `_publish_heartbeat_loop` | `agent_status status=running` |
| Peer-stale heartbeat scan | every 2 s | each AGV `_scan_for_faults` | `task_orphan` if peer silent > 8 s |
| Master deadline resolver | every 1 s | `MasterAnnouncer._tick` | `claim` or `task_announce (retry)` if conditions met |

## Phase G — Fault recovery (only fires if an AGV dies mid-task)

| # | What happens | Who decides / How | Bus emission |
|---|---|---|---|
| F1 | AGV-X dies (kill, crash, network drop) — stops emitting `agent_status` | none | — |
| F2 | First surviving peer's `_scan_for_faults` notices > 8 s of silence from AGV-X | script — peer heartbeat watch | — |
| F3 | First peer publishes orphan; bus dedups by `task_id` so only one wins the "announcer" role | script — bus dedup | **`task_orphan`** |
| F4 | Each surviving peer's `_on_orphan` spawns a bid thread (same as Phase B step 9) | script (threading) | — |
| F5 | Each surviving peer evaluates fitness for the orphaned task | **qwen14b @ peer-N** | — |
| F6 | Surviving peers publish bids | script | **`bid` per peer** |
| F7 | Announcer-peer collects bids; if no peer bid and announcer is idle, self-rescues; otherwise picks max-score | script — **same contract-net + spread logic, but the announcer is a peer not master** | — |
| F8 | Announcer-peer publishes claim | script | **`claim`** (from a peer this time, not master) |
| F9 | Winner's `_on_claim` → `_take_task` → brain runs the orphaned task | script | `agent_status status=running` etc. |
| F10 | Central brain stays silent the entire time — its `_on_agv_fault` is unbound in decentralized mode | guarded at startup in `app/__init__.py` | — |

---

## Consolidated AI Actions

Every distinct moment the system calls an LLM, in execution order, for a clean 3-package happy-path run. Everything else in the workflow above is deterministic script.

| Stage | Description (which model, where) | Action taken |
|---|---|---|
| A1 | **qwq:32b @ master** reads the user's natural-language prompt during the `planning_window` | Parses prompt into a structured TaskSpec — `{intent, package_count, ordering, agv_ids}` |
| A2 | **qwq:32b @ master** runs in the same warm load (no GPU swap), with the TaskSpec | Generates `assignments[]` — one entry per package with `agv_hint`, `label`, `sequence`, `route` |
| B1 | **qwen14b @ each AGV** (concurrent thread, one per announced package) reads its own state + the snapshot | Produces a bid `{score, rationale, eta_s}` per package this AGV decides it wants — natural-language rationale published on the bus verbatim |
| C1 | **qwen14b @ winning AGV** before `acquire_input_station` — sees lane_holder, station_holders, other AGV positions | Returns `proceed` / `hold` / `fault` — decides whether to attempt the lock now |
| C2 | **qwen14b @ input arm** before sweeping — `_arm_ai_gate(arm-1, agv-N, "load")` | Returns `go` / `wait` — decides whether the dock is actually ready for the load sweep |
| C3 | **qwen14b @ winning AGV** before `acquire_lane` — sees if another AGV holds the shared corridor | Returns `proceed` / `hold` |
| E1 | **qwen14b @ winning AGV** before `move_to_output` — sees output station_holder and whether the route is clear | Returns `proceed` / `hold` |
| E2 | **qwen14b @ output arm** before sweeping — `_arm_ai_gate(arm-2, agv-N, "unload")` | Returns `go` / `wait` |
| F1 | **qwen14b @ winning AGV** before `return_to_bay` | Returns `proceed` / `hold` |
| G1 | **qwen14b @ each surviving peer** if an AGV dies mid-task — evaluates the orphaned package the same way as Phase B | Produces a bid for the orphaned task; the announcer-peer runs the contract-net + spread logic and publishes the claim |

### Counts at a glance

For a clean 3-package, 3-AGV run with no faults:

| AI consultations | Count | Notes |
|---|---|---|
| qwq:32b @ master | **2** | interpret + plan, back-to-back inside the same warm load |
| qwen14b @ each AGV — bid phase | **~9** total | 3 AGVs × up to 3 packages each |
| qwen14b @ each AGV — per-step gates | **~18** total | ~6 per running AGV × 3 AGVs (acquire input, lane, output, return, etc.) |
| qwen14b @ each arm — sweep gates | **~6** total | 2 arms × 3 packages (one load gate + one unload gate per package) |
| **Total LLM calls per 3-package run** | **~35** | of which all but 2 are tactical 14b on the shared TUC GPU |

Bus events per package, end-to-end (no faults):

| Event type | Per package | Notes |
|---|---|---|
| task_announce | 1 | master only |
| bid | ~3 | one per AGV that decides to bid |
| claim | 1 | master only |
| agent_status running heartbeats | ~20 | every 3 s while AGV is running |
| intent | ~4 | move_to_input, acquire_lane, move_to_output, plus optional |
| arm_approach | 2 | input + output |
| arm_ready | 2 | loaded + unloaded |
| agent_status done | 1 | at end |
| **Total per package** | **~34** | of which ~20 are heartbeats hidden by the UI filter |

A live 3-package run logged on 22 May 2026 produced 158 total bus events. After filtering out the 122 `agent_status running` heartbeats, ~36 meaningful events remained — matching the per-package counts above.

---

## What is AI, what is script, what is the bus — one-liner each

- **AI does** the things that benefit from judgment: understanding free-form prompts, deciding whether to bid (and how high), deciding whether the dock is safe to enter, deciding whether to hold while another AGV holds the lane. Two models, on a shared GPU, never both resident at once.
- **Script does** the things that benefit from determinism: walking cell-by-cell along a known route, acquiring locks, sweeping the arm through fixed angles, publishing the protocol envelope to the bus, polling status endpoints. Failure-safe rails the AI cannot override.
- **The message bus carries** the protocol: `task_announce`, `bid`, `claim`, `task_orphan`, `agent_status`, `intent`, `arm_approach`, `arm_ready`, `yield`. Everything an outside observer needs to see what the agents decided and why.
- **Contract-net is** the rulebook (announce → bid → claim) that turns independent AI bids into a fair allocation. **Spread is** the extension that prevents one AGV from winning every tied bid. Both are deterministic Python in the master.

## Files in this work

- `app/routes/bus_routes.py` — HTTP ring buffer message bus
- `app/agents/master_announcer.py` — backend announcer, bid resolver, spread set, re-announce path
- `scripts/agv_bus.py` — per-AGV BusListener (three daemon threads: bus poll, heartbeat watch, self-heartbeat publish)
- `scripts/agv_brain.py` — AGV decision loop, intent/arm_approach/yield emit hooks
- `scripts/agv_server.py` — `_is_busy`, `_take_task`, `_current_state` callbacks
- `app/routes/dispatch_routes.py` — arm choreography + arm_ready publish + `_arm_ai_gate`
- `app/routes/ai_routes.py` — `/plan` routing, `/ai/tactical` endpoint
- `app/agents/central_brain.py` — gated off in decentralized mode
- `app/__init__.py`, `pipeline.yaml`, `scripts/generate_compose.py`, `docker-compose.yml`, `docker/nginx.conf` — feature flag plumbing
- `docs/decentralized-multi-agent-coordination-(27 May 2026).md` — architecture write-up + 22 May bug-fix transcripts (read together with this document)
- `docs/coordination-phase{1,2,3}-evidence.md` + `coordination-final-evidence.md` — verbatim live-run transcripts
- `docs/tuc-setup-and-run.md` — operational runbook (SSH tunnel + Docker stack)
